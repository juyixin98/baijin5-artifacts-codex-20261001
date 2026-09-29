//! Stateful paging: sessions hold a prepared join and a cursor; continuation
//! requests are validated against the session and resume from the exact
//! checkpoint the previous page reported. This is the controlled-batching
//! counterpart to "output over budget" — truncated work is never lost.
//!
//! Error mapping:
//! * unknown/expired session id      → [`ErrorCode::UnknownSession`]
//! * cursor bound to another session → [`ErrorCode::CursorMismatch`]
//! * continuation after completion   → [`ErrorCode::SessionFinished`]
//! * registry capacity reached       → [`ErrorCode::SessionLimitReached`]

use std::collections::HashMap;
use std::sync::Mutex;

use serde::{Deserialize, Serialize};

use crate::error::{ErrorCode, JoinError, JoinResult};
use crate::operator::{Checkpoint, JoinPage, JoinPlan, PreparedJoin};
use crate::replay::BatchFingerprint;
use crate::resource::{Budget, Truncation};

/// Immutable metadata kept with a session so every continuation page can be
/// replayed/reconstructed without the client resending the inputs.
#[derive(Debug, Clone)]
pub struct SessionMeta {
    pub plan: JoinPlan,
    pub budget: Budget,
    pub left: BatchFingerprint,
    pub right: BatchFingerprint,
}

/// Server-side session record.
struct Session {
    prepared: PreparedJoin,
    meta: SessionMeta,
    checkpoint: Checkpoint,
    finished: bool,
    pages_emitted: usize,
}

/// Thread-safe registry with a hard capacity.
pub struct SessionRegistry {
    sessions: Mutex<HashMap<String, Session>>,
    capacity: usize,
}

impl SessionRegistry {
    pub fn new(capacity: usize) -> Self {
        Self {
            sessions: Mutex::new(HashMap::new()),
            capacity,
        }
    }

    pub fn create(
        &self,
        session_id: impl Into<String>,
        prepared: PreparedJoin,
        meta: SessionMeta,
    ) -> JoinResult<()> {
        let mut sessions = self.sessions.lock().expect("session registry poisoned");
        if sessions.len() >= self.capacity {
            return Err(JoinError::resource(
                ErrorCode::SessionLimitReached,
                format!("session registry full ({})", self.capacity),
            ));
        }
        sessions.insert(
            session_id.into(),
            Session {
                prepared,
                meta,
                checkpoint: Checkpoint::start(),
                finished: false,
                pages_emitted: 0,
            },
        );
        Ok(())
    }

    /// Produce the first page for a freshly created session.
    pub fn first_page(&self, session_id: &str) -> JoinResult<JoinPage> {
        let mut sessions = self.sessions.lock().expect("session registry poisoned");
        let session = sessions.get_mut(session_id).ok_or_else(|| {
            JoinError::state(
                ErrorCode::UnknownSession,
                format!("session '{session_id}' not found"),
            )
        })?;
        if session.finished || session.pages_emitted != 0 {
            return Err(JoinError::state(
                ErrorCode::CursorMismatch,
                "first_page called on a session already in progress",
            ));
        }
        let page = session
            .prepared
            .run_page(&session.meta.budget, Checkpoint::start());
        session.checkpoint = page.next;
        session.pages_emitted = 1;
        session.finished = page.finished;
        Ok(page)
    }

    /// Produce a continuation page. The cursor's `page_index` is the number
    /// of pages already delivered; it must match the session exactly, which
    /// rejects replays/skips/stale cursors as state conflicts.
    pub fn next_page(&self, session_id: &str, cursor: &Cursor) -> JoinResult<JoinPage> {
        if cursor.session_id != session_id {
            return Err(JoinError::state(
                ErrorCode::CursorMismatch,
                format!(
                    "cursor belongs to session '{}', not '{}'",
                    cursor.session_id, session_id
                ),
            ));
        }
        let mut sessions = self.sessions.lock().expect("session registry poisoned");
        let session = sessions.get_mut(session_id).ok_or_else(|| {
            JoinError::state(
                ErrorCode::UnknownSession,
                format!("session '{session_id}' not found"),
            )
        })?;
        if session.finished {
            return Err(JoinError::state(
                ErrorCode::SessionFinished,
                format!(
                    "session '{session_id}' already finished after {} pages",
                    session.pages_emitted
                ),
            ));
        }
        if cursor.page_index != session.pages_emitted {
            return Err(JoinError::state(
                ErrorCode::CursorMismatch,
                format!(
                    "stale/out-of-order cursor: cursor says {} pages delivered, session has {}",
                    cursor.page_index, session.pages_emitted
                ),
            ));
        }
        let page = session
            .prepared
            .run_page(&session.meta.budget, cursor.checkpoint);
        session.checkpoint = page.next;
        session.pages_emitted += 1;
        session.finished = page.finished;
        Ok(page)
    }

    /// Immutable metadata for replay records (continuation pages).
    pub fn meta(&self, session_id: &str) -> Option<SessionMeta> {
        self.sessions
            .lock()
            .expect("session registry poisoned")
            .get(session_id)
            .map(|s| s.meta.clone())
    }

    pub fn drop_session(&self, session_id: &str) -> bool {
        self.sessions
            .lock()
            .expect("session registry poisoned")
            .remove(session_id)
            .is_some()
    }

    pub fn len(&self) -> usize {
        self.sessions
            .lock()
            .expect("session registry poisoned")
            .len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

/// Continuation token handed to clients (JSON; carries no secrets).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Cursor {
    pub session_id: String,
    pub page_index: usize,
    pub checkpoint: Checkpoint,
    pub truncation: Truncation,
}
