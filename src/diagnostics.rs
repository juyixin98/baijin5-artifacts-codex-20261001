//! Request-scoped diagnostics with explicit accept/reject/undecidable
//! decisions and data redaction.
//!
//! Every query produces one [`DecisionRecord`] carrying a correlation id and
//! the *key state* behind the outcome (shape, relation sizes, join
//! attributes, engine counters, failure category). Actual cell values are
//! never logged: fixtures may stand in for sensitive business data, and the
//! discipline must hold regardless. Redaction renders a value as its type
//! (and, for strings, length), never the content.

use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::Serialize;

use crate::error::ErrorCode;
use crate::lftj::StopReason;
use crate::value::Scalar;

static SEQUENCE: AtomicU64 = AtomicU64::new(1);

/// Correlation id attached to logs and response bodies.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RequestId(pub String);

impl RequestId {
    pub fn new() -> Self {
        let seq = SEQUENCE.fetch_add(1, Ordering::Relaxed);
        RequestId(format!("req-{seq:08}-{}", uuid_prefix()))
    }
}

impl Default for RequestId {
    fn default() -> Self {
        Self::new()
    }
}

impl std::fmt::Display for RequestId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

/// Short random suffix from the local uuid crate (no network involved).
fn uuid_prefix() -> String {
    uuid::Uuid::new_v4().simple().to_string()[..8].to_string()
}

/// The three-way verdict required by the spec: accepted, rejected, or
/// "cannot decide yet" (e.g. access budget exhausted).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Decision {
    Accepted,
    Rejected,
    Undecidable,
}

/// One human- and machine-readable reason behind a decision.
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct Reason {
    pub code: String,
    pub detail: String,
}

/// Shape/counters worth recording; contains no cell data.
#[derive(Debug, Clone, Serialize, Default, PartialEq, Eq)]
pub struct KeyState {
    pub relations: Vec<RelationState>,
    pub output_arity: usize,
    pub join_attributes: Vec<String>,
    pub null_policy: String,
    pub emitted_rows: u64,
    pub skipped_rows: u64,
    pub intermediate_tuples_materialized: u64,
    pub trie_seeks: u64,
    pub trie_seek_comparisons: u64,
    pub trie_nexts: u64,
    pub trie_child_opens: u64,
    pub stop_reason: Option<String>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct RelationState {
    pub name: String,
    pub rows: u64,
    pub columns: Vec<String>,
}

/// The full record emitted once per request.
#[derive(Debug, Clone, Serialize)]
pub struct DecisionRecord {
    pub request_id: String,
    pub timestamp_unix_ms: u128,
    pub decision: Decision,
    pub reasons: Vec<Reason>,
    pub state: KeyState,
    /// Stable failure category when rejected/undecidable.
    pub error_code: Option<String>,
}

impl DecisionRecord {
    pub fn accepted(request_id: &RequestId, state: KeyState, reasons: Vec<Reason>) -> Self {
        let record = DecisionRecord {
            request_id: request_id.0.clone(),
            timestamp_unix_ms: now_ms(),
            decision: Decision::Accepted,
            reasons,
            state,
            error_code: None,
        };
        record.log();
        record
    }

    pub fn rejected(
        request_id: &RequestId,
        state: KeyState,
        code: ErrorCode,
        detail: String,
    ) -> Self {
        let record = DecisionRecord {
            request_id: request_id.0.clone(),
            timestamp_unix_ms: now_ms(),
            decision: Decision::Rejected,
            reasons: vec![Reason {
                code: code.as_str().to_string(),
                detail,
            }],
            state,
            error_code: Some(code.as_str().to_string()),
        };
        record.log();
        record
    }

    pub fn undecidable(request_id: &RequestId, state: KeyState, reasons: Vec<Reason>) -> Self {
        let record = DecisionRecord {
            request_id: request_id.0.clone(),
            timestamp_unix_ms: now_ms(),
            decision: Decision::Undecidable,
            reasons,
            state,
            error_code: Some(ErrorCode::Internal.as_str().to_string()),
        };
        record.log();
        record
    }

    fn log(&self) {
        match self.decision {
            Decision::Accepted => tracing::info!(
                request_id = %self.request_id,
                decision = "accepted",
                emitted = self.state.emitted_rows,
                seeks = self.state.trie_seeks,
                intermediate = self.state.intermediate_tuples_materialized,
                stop = self.state.stop_reason.as_deref().unwrap_or("unknown"),
                "query accepted"
            ),
            Decision::Rejected => tracing::warn!(
                request_id = %self.request_id,
                decision = "rejected",
                error_code = self.error_code.as_deref().unwrap_or("invalid_request"),
                reasons = %format_reasons(&self.reasons),
                "query rejected"
            ),
            Decision::Undecidable => tracing::warn!(
                request_id = %self.request_id,
                decision = "undecidable",
                stop = self.state.stop_reason.as_deref().unwrap_or("unknown"),
                "query could not be decided"
            ),
        }
    }

    /// Map an engine stop reason to the three-way verdict.
    pub fn decision_for_stop(stop: StopReason) -> Decision {
        match stop {
            StopReason::Complete | StopReason::OutputLimit => Decision::Accepted,
            StopReason::BudgetExhausted => Decision::Undecidable,
        }
    }
}

fn now_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0)
}

/// Compact, single-line rendering of decision reasons for structured logs.
fn format_reasons(reasons: &[Reason]) -> String {
    reasons
        .iter()
        .map(|r| format!("[{}] {}", r.code, r.detail))
        .collect::<Vec<_>>()
        .join("; ")
}

/// Redact a scalar for any diagnostic surface: type plus coarse shape only.
pub fn redact(value: &Scalar) -> String {
    match value {
        Scalar::Int(_) => "<int>".to_string(),
        Scalar::Bool(_) => "<bool>".to_string(),
        Scalar::Str(s) => format!("<string:len={}>", s.chars().count()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn request_ids_are_unique_and_correlatable() {
        let a = RequestId::new();
        let b = RequestId::new();
        assert_ne!(a, b);
        assert!(a.0.starts_with("req-"));
    }

    #[test]
    fn redaction_never_emits_content() {
        assert_eq!(redact(&Scalar::Int(-42)), "<int>");
        assert_eq!(redact(&Scalar::Bool(true)), "<bool>");
        assert_eq!(
            redact(&Scalar::Str("super-secret".to_string())),
            "<string:len=12>"
        );
    }

    #[test]
    fn record_is_serialisable_without_cell_data() {
        let record = DecisionRecord::rejected(
            &RequestId::new(),
            KeyState::default(),
            ErrorCode::NullKey,
            "NULL in join key 'a'".to_string(),
        );
        let json = serde_json::to_string(&record).unwrap();
        assert!(json.contains("rejected"));
        assert!(json.contains("null_key"));
        assert!(!json.contains("super-secret"));
    }
}
