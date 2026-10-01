//! Request-scoped diagnostics.
//!
//! Every accepted query, rejected plan and undeterminable resumption produces a
//! [`Decision`] record carrying the request id, the verdict, the reason and
//! *redacted* key state (counts and limits only — never cell values).  Records
//! are emitted through `tracing` and returned in the response envelope so a
//! reviewer can see *why* the engine accepted, rejected or could not decide.

use serde::Serialize;

/// The three verdicts a request can receive.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Verdict {
    /// Plan validated (quantiles in range, schema sound) and execution finished.
    Accepted,
    /// Rejected before executing any operator (bad quantile, bad schema, ...).
    Rejected,
    /// Execution could not reach a result (cancellation, budget, I/O) but
    /// durable spill state may allow a retry.
    Undetermined,
}

/// Redacted snapshot of the state a decision was made on.  All fields are
/// counts/sizes/flags — user data never appears here.
#[derive(Debug, Clone, Default, Serialize)]
pub struct KeyState {
    #[serde(default)]
    pub ingested_rows: u64,
    #[serde(default)]
    pub spill_runs: usize,
    #[serde(default)]
    pub peak_memory_bytes: usize,
    #[serde(default)]
    pub memory_budget_bytes: usize,
    #[serde(default)]
    pub groups_seen: usize,
    #[serde(default)]
    pub spill_complete: bool,
    #[serde(default)]
    pub resumable: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct Decision {
    pub request_id: String,
    pub query_id: String,
    pub verdict: Verdict,
    pub reason: String,
    pub error_kind: Option<String>,
    pub key_state: KeyState,
}

impl Decision {
    pub fn accepted(
        request_id: &str,
        query_id: &str,
        reason: impl Into<String>,
        state: KeyState,
    ) -> Self {
        Self {
            request_id: request_id.to_string(),
            query_id: query_id.to_string(),
            verdict: Verdict::Accepted,
            reason: reason.into(),
            error_kind: None,
            key_state: state,
        }
    }

    pub fn rejected(
        request_id: &str,
        query_id: &str,
        reason: impl Into<String>,
        kind: String,
    ) -> Self {
        Self {
            request_id: request_id.to_string(),
            query_id: query_id.to_string(),
            verdict: Verdict::Rejected,
            reason: reason.into(),
            error_kind: Some(kind),
            key_state: KeyState::default(),
        }
    }

    pub fn undetermined(
        request_id: &str,
        query_id: &str,
        reason: impl Into<String>,
        kind: String,
        state: KeyState,
    ) -> Self {
        Self {
            request_id: request_id.to_string(),
            query_id: query_id.to_string(),
            verdict: Verdict::Undetermined,
            reason: reason.into(),
            error_kind: Some(kind),
            key_state: state,
        }
    }

    /// Emit the decision at the appropriate tracing level.  Only redacted
    /// state is logged — see [`KeyState`].
    pub fn trace(&self) {
        let target = "groupagg::diagnostics";
        match self.verdict {
            Verdict::Accepted => tracing::info!(
                target,
                request_id = %self.request_id,
                query_id = %self.query_id,
                verdict = ?self.verdict,
                rows = self.key_state.ingested_rows,
                runs = self.key_state.spill_runs,
                peak_bytes = self.key_state.peak_memory_bytes,
                groups = self.key_state.groups_seen,
                "{}", self.reason
            ),
            Verdict::Rejected => tracing::warn!(
                target,
                request_id = %self.request_id,
                query_id = %self.query_id,
                verdict = ?self.verdict,
                error_kind = ?self.error_kind,
                "{}", self.reason
            ),
            Verdict::Undetermined => tracing::warn!(
                target,
                request_id = %self.request_id,
                query_id = %self.query_id,
                verdict = ?self.verdict,
                error_kind = ?self.error_kind,
                rows = self.key_state.ingested_rows,
                runs = self.key_state.spill_runs,
                spill_complete = self.key_state.spill_complete,
                resumable = self.key_state.resumable,
                "{}", self.reason
            ),
        }
    }
}

/// Generate a short, printable request identifier.  It is not a secret but
/// keeps logs linkable without exposing endpoint internals.
pub fn new_request_id() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let random = NANO_COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    format!("req-{nanos:016x}-{random:04x}")
}

static NANO_COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// Redact a free-text field for safe logging: keep the length and a small
/// prefix hash, never the content.  Used for any user-supplied identifier that
/// might carry sensitive meaning.
pub fn redact_label(label: &str) -> String {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    let mut h = DefaultHasher::new();
    label.hash(&mut h);
    format!(
        "<{} chars:hash{:08x}>",
        label.chars().count(),
        h.finish() as u32
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn request_ids_are_distinct() {
        assert_ne!(new_request_id(), new_request_id());
    }

    #[test]
    fn redaction_never_contains_original() {
        let secret = "customer-pii-12345";
        let redacted = redact_label(secret);
        assert!(!redacted.contains(secret));
        assert!(redacted.contains("18 chars"));
    }

    #[test]
    fn decision_serializes_verdict_and_state() {
        let d = Decision::rejected("r1", "q1", "bad q", "INVALID_QUANTILE".into());
        let json = serde_json::to_value(&d).unwrap();
        assert_eq!(json["verdict"], "rejected");
        assert_eq!(json["error_kind"], "INVALID_QUANTILE");
        assert_eq!(json["key_state"]["ingested_rows"], 0);
    }
}
