//! Process resources and mutable state.
//!
//! Two pieces of real state live here behind the Axum `State` extractor:
//!
//! 1. [`DiagLog`] — a bounded, in-memory record of every verification decision
//!    (accepted / rejected / undetermined) keyed by request id. Values are
//!    stored redacted.
//! 2. [`Sessions`] — stateful cross-batch aggregation sessions. A session is
//!    pinned to the rule version of its first batch; feeding it a batch from a
//!    different version is rejected, enforcing "different rule versions never
//!    mix" above and beyond the per-key guard.

use std::collections::{HashMap, VecDeque};
use std::hash::{Hash, Hasher};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;

use crate::config::Settings;
use crate::operators::ExecOutput;

/// Why a stateful operation was refused.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum StateError {
    /// A batch keyed under `incoming` was added to a session pinned to
    /// `existing`.
    RuleVersionMismatch {
        existing: u16,
        incoming: u16,
    },
    UnknownSession(String),
}

impl std::fmt::Display for StateError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            StateError::RuleVersionMismatch { existing, incoming } => write!(
                f,
                "rule version mismatch: session pinned to v{existing}, batch is v{incoming}"
            ),
            StateError::UnknownSession(id) => write!(f, "unknown session {id:?}"),
        }
    }
}

/// One redacted decision record.
#[derive(Clone, Debug, serde::Serialize)]
pub struct DiagRecord {
    pub request_id: String,
    pub session_id: Option<String>,
    pub rule_version: Option<u16>,
    /// One-word status: accepted | rejected | undetermined.
    pub status: &'static str,
    /// Stable machine code for the failure/accept class.
    pub reason_code: String,
    /// Human explanation (never contains raw sensitive values).
    pub detail: String,
    pub rows: usize,
    pub groups: Option<usize>,
}

/// Bounded append-only diagnostic log.
#[derive(Debug)]
pub struct DiagLog {
    cap: usize,
    records: Mutex<VecDeque<DiagRecord>>,
}

impl DiagLog {
    pub fn new(cap: usize) -> Self {
        DiagLog {
            cap: cap.max(1),
            records: Mutex::new(VecDeque::with_capacity(cap.max(1))),
        }
    }

    pub fn push(&self, rec: DiagRecord) {
        let mut q = self.records.lock().expect("diag lock poisoned");
        if q.len() == self.cap {
            q.pop_front();
        }
        q.push_back(rec);
    }

    /// Most recent records, oldest first.
    pub fn recent(&self, n: usize) -> Vec<DiagRecord> {
        let q = self.records.lock().expect("diag lock poisoned");
        q.iter().rev().take(n).rev().cloned().collect()
    }
}

/// A stateful aggregation session pinned to one rule version.
#[derive(Clone, Debug)]
pub struct Session {
    pub rule_version: u16,
    pub batches: usize,
    pub rows: usize,
    /// Accumulated group count across batches (equivalence classes are merged
    /// by the caller via [`Sessions::merge`]).
    pub merged_groups: usize,
}

#[derive(Debug, Default)]
pub struct Sessions {
    inner: Mutex<HashMap<String, Session>>,
}

impl Sessions {
    /// Add a batch to a session. The first batch pins the rule version; later
    /// batches must match.
    pub fn add_batch(
        &self,
        session_id: &str,
        rule_version: u16,
        rows: usize,
    ) -> Result<Session, StateError> {
        let mut map = self.inner.lock().expect("sessions lock poisoned");
        match map.get_mut(session_id) {
            Some(s) => {
                if s.rule_version != rule_version {
                    return Err(StateError::RuleVersionMismatch {
                        existing: s.rule_version,
                        incoming: rule_version,
                    });
                }
                s.batches += 1;
                s.rows += rows;
                Ok(s.clone())
            }
            None => {
                let s = Session {
                    rule_version,
                    batches: 1,
                    rows,
                    merged_groups: 0,
                };
                map.insert(session_id.to_string(), s.clone());
                Ok(s)
            }
        }
    }

    /// Fold one batch's groups into the session, again enforcing the version.
    pub fn merge(
        &self,
        session_id: &str,
        rule_version: u16,
        out: &ExecOutput,
    ) -> Result<usize, StateError> {
        let mut map = self.inner.lock().expect("sessions lock poisoned");
        let s = map
            .get_mut(session_id)
            .ok_or_else(|| StateError::UnknownSession(session_id.into()))?;
        if s.rule_version != rule_version {
            return Err(StateError::RuleVersionMismatch {
                existing: s.rule_version,
                incoming: rule_version,
            });
        }
        s.merged_groups += out.group_count();
        Ok(s.merged_groups)
    }

    pub fn get(&self, session_id: &str) -> Option<Session> {
        self.inner
            .lock()
            .expect("sessions lock poisoned")
            .get(session_id)
            .cloned()
    }
}

/// Shared application state.
#[derive(Debug)]
pub struct AppState {
    pub settings: Settings,
    pub diag: DiagLog,
    pub sessions: Sessions,
    seq: AtomicU64,
}

impl AppState {
    pub fn new(settings: Settings) -> Self {
        let cap = settings.diag_history;
        AppState {
            diag: DiagLog::new(cap),
            sessions: Sessions::default(),
            settings,
            seq: AtomicU64::new(1),
        }
    }

    /// Generate a process-unique request id.
    pub fn new_request_id(&self) -> String {
        let n = self.seq.fetch_add(1, Ordering::Relaxed);
        // millis-since-epoch-ish prefix without pulling a clock dependency
        format!("req-{n:08}")
    }
}

/// Redact a value for logging. With redaction on, no raw characters leave the
/// process — only the byte length and a short non-reversible fingerprint.
pub fn redact(value: Option<&str>, enabled: bool) -> String {
    match value {
        None => "<null>".into(),
        Some(v) if !enabled => v.chars().take(64).collect(),
        Some(v) => {
            let mut h = std::collections::hash_map::DefaultHasher::new();
            v.hash(&mut h);
            let fp = h.finish();
            format!("<str bytes={} fp={:016x}>", v.len(), fp)
        }
    }
}
