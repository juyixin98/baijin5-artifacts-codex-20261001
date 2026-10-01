//! Diagnostics: every accept/reject/undetermined decision carries a request id,
//! the stage it happened at, the key state observed, and a machine-readable reason.
//!
//! Sensitive values are never logged verbatim: only a non-reversible token (length +
//! FNV fingerprint) is emitted unless redaction is explicitly disabled in config.

use serde::{Deserialize, Serialize};

use crate::collation::fnv1a_64;

/// Outcome category for a validation stage.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Decision {
    /// Inputs accepted and the two executors agreed (or single executor succeeded).
    Accepted,
    /// Rejected for a concrete, typed reason.
    Rejected,
    /// Could not decide (e.g. unknown rule version that is neither allow-listed).
    Undetermined,
}

/// Typed failure categories asserted by tests.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum FailureCategory {
    /// Rule version string is not in the registry.
    UnknownRuleVersion { supplied: String },
    /// One grouping operation was asked to mix values tagged with different versions.
    RuleVersionMixed { versions: Vec<String> },
    /// Column name missing/empty.
    EmptyColumn,
    /// A row was not a string / missing.
    InvalidRow { index: usize, reason: String },
    /// Sort-agg and hash-agg produced different partitions.
    ExecutorMismatch { detail: String },
    /// Independent oracle disagreed with the core result.
    OracleMismatch { detail: String },
}

impl std::fmt::Display for FailureCategory {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            FailureCategory::UnknownRuleVersion { supplied } => {
                write!(f, "unknown_rule_version({supplied})")
            }
            FailureCategory::RuleVersionMixed { versions } => {
                write!(f, "rule_version_mixed({})", versions.join(","))
            }
            FailureCategory::EmptyColumn => f.write_str("empty_column"),
            FailureCategory::InvalidRow { index, reason } => {
                write!(f, "invalid_row[{index}]: {reason}")
            }
            FailureCategory::ExecutorMismatch { detail } => {
                write!(f, "executor_mismatch: {detail}")
            }
            FailureCategory::OracleMismatch { detail } => {
                write!(f, "oracle_mismatch: {detail}")
            }
        }
    }
}

/// One structured diagnostic record.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DiagRecord {
    pub request_id: String,
    pub stage: String,
    pub decision: Decision,
    /// Key state at the point of decision (redacted, no raw sensitive values).
    pub state: Vec<(String, String)>,
    /// Present when decision != accepted.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub failure: Option<FailureCategory>,
    pub reason: String,
}

impl DiagRecord {
    pub fn accepted(request_id: &str, stage: &str, reason: impl Into<String>) -> Self {
        Self {
            request_id: request_id.to_owned(),
            stage: stage.to_owned(),
            decision: Decision::Accepted,
            state: Vec::new(),
            failure: None,
            reason: reason.into(),
        }
    }

    pub fn rejected(request_id: &str, stage: &str, failure: FailureCategory) -> Self {
        let reason = failure.to_string();
        Self {
            request_id: request_id.to_owned(),
            stage: stage.to_owned(),
            decision: Decision::Rejected,
            state: Vec::new(),
            failure: Some(failure),
            reason,
        }
    }

    pub fn undetermined(request_id: &str, stage: &str, reason: impl Into<String>) -> Self {
        Self {
            request_id: request_id.to_owned(),
            stage: stage.to_owned(),
            decision: Decision::Undetermined,
            state: Vec::new(),
            failure: None,
            reason: reason.into(),
        }
    }

    pub fn with(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.state.push((key.into(), value.into()));
        self
    }
}

/// Redact a potentially sensitive value into a non-reversible token.
///
/// Returns `len=<n>,fp=<8hex>`; the fingerprint lets tests correlate repeated values
/// without ever exposing content.
pub fn redact(value: &str) -> String {
    let fp = fnv1a_64(value.as_bytes()) & 0xffff_ffff;
    format!("len={},fp={:08x}", value.chars().count(), fp)
}

/// New request id (v4 uuid string).
pub fn new_request_id() -> String {
    uuid::Uuid::new_v4().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redaction_never_contains_input() {
        let secret = "super-secret-ACME-42";
        let r = redact(secret);
        assert!(!r.contains("ACME"));
        assert!(!r.contains("secret"));
        assert!(r.starts_with("len="));
    }

    #[test]
    fn redaction_is_stable_and_length_aware() {
        assert_eq!(redact("abc"), redact("abc"));
        assert_ne!(redact("abc"), redact("abcd"));
    }
}
