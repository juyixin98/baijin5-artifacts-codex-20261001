//! Resumable execution state and server-side cursor sessions.
//!
//! A join can be consumed in controlled batches. The operator is
//! suspended after each page via [`ExecutionState`], an opaque,
//! serializable checkpoint of *where enumeration was* — nothing derived
//! from result values. On resume the deterministic sort permutations
//! are rebuilt and enumeration continues exactly once, so no pair is
//! emitted twice or skipped.
//!
//! [`SessionStore`] keeps the server-side records (inputs, plan,
//! checkpoint) behind opaque cursor ids. Resuming with a closed or
//! unknown cursor, or changing the inputs behind a cursor, is a
//! `StateConflict`.

use std::collections::HashMap;
use std::sync::Mutex;

use serde::{Deserialize, Serialize};

use crate::batch::Batch;
use crate::error::{JoinError, JoinResult};
use crate::operator::plan::JoinPlan;
use crate::resource::Budget;

/// Checkpoint of a suspended IEJoin enumeration.
///
/// Positions refer to the deterministic permutations rebuilt from the
/// same plan and inputs.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ExecutionState {
    /// Index in the right scan permutation to process next.
    pub scan_pos: u32,
    /// Current boundary of the monotone gate in left permutation 1.
    pub gate_cursor: u32,
    /// Candidate bitmap words (left permutation-2 positions).
    pub bitmap_words: Vec<u64>,
    pub bitmap_len: u32,
    /// When resuming mid-right-row, the first permutation-2 position to
    /// inspect; `u32::MAX` means "start a fresh right row".
    pub resume_pos2: u32,
    /// Total pairs emitted across all pages so far.
    pub emitted: u64,
}

/// One server-side cursor: the immutable query definition plus its
/// mutable checkpoint.
pub struct CursorSession {
    pub run_id: String,
    pub plan: JoinPlan,
    pub left: Batch,
    pub right: Batch,
    pub budget: Budget,
    pub state: ExecutionState,
    pub finished: bool,
    /// Number of pages already yielded to the client.
    pub batches_yielded: u64,
    /// Fingerprint of plan+inputs, used to reject mid-flight tampering.
    pub fingerprint: String,
}

/// Process-wide, mutex-protected session table. Small and local; no
/// external store.
#[derive(Default)]
pub struct SessionStore {
    inner: Mutex<HashMap<String, CursorSession>>,
}

impl SessionStore {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Insert a fresh session, returning its cursor id.
    pub fn create(&self, id: impl Into<String>, session: CursorSession) {
        self.inner
            .lock()
            .expect("session lock poisoned")
            .insert(id.into(), session);
    }

    /// Read-only access to a session.
    ///
    /// # Errors
    /// `StateConflict` (`unknown_cursor`) when absent.
    pub fn with<T>(&self, id: &str, f: impl FnOnce(&CursorSession) -> T) -> JoinResult<T> {
        let guard = self.inner.lock().expect("session lock poisoned");
        let s = guard.get(id).ok_or_else(|| unknown_cursor(id))?;
        Ok(f(s))
    }

    /// Mutable access to a session.
    ///
    /// # Errors
    /// `StateConflict` (`unknown_cursor`) when absent.
    pub fn with_mut<T>(&self, id: &str, f: impl FnOnce(&mut CursorSession) -> T) -> JoinResult<T> {
        let mut guard = self.inner.lock().expect("session lock poisoned");
        let s = guard.get_mut(id).ok_or_else(|| unknown_cursor(id))?;
        Ok(f(s))
    }

    /// Remove a finished/expired session.
    pub fn remove(&self, id: &str) -> Option<CursorSession> {
        self.inner.lock().expect("session lock poisoned").remove(id)
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.inner.lock().expect("session lock poisoned").len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

fn unknown_cursor(id: &str) -> JoinError {
    JoinError::conflict("unknown_cursor", format!("no cursor with id '{id}'"))
        .with("cursor_id", serde_json::json!(id))
}

/// Reject advancing a cursor that has already reported completion.
///
/// # Errors
/// `StateConflict` (`cursor_closed`) when `finished` is true.
pub fn ensure_open(session: &CursorSession) -> JoinResult<()> {
    if session.finished {
        return Err(JoinError::conflict(
            "cursor_closed",
            "cursor has already delivered its final page",
        )
        .with("cursor_run_id", serde_json::json!(session.run_id)));
    }
    Ok(())
}

/// Reject continuing a cursor under inputs/plan that do not match the
/// fingerprint captured at creation.
///
/// # Errors
/// `StateConflict` (`input_changed`) on mismatch.
pub fn ensure_fingerprint(session: &CursorSession, fingerprint: &str) -> JoinResult<()> {
    if session.fingerprint != fingerprint {
        return Err(JoinError::conflict(
            "input_changed",
            "cursor cannot be advanced with different plan or inputs",
        )
        .with("expected", serde_json::json!(session.fingerprint))
        .with("actual", serde_json::json!(fingerprint)));
    }
    Ok(())
}
