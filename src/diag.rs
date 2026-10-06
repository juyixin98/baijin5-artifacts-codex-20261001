//! Diagnostics: structured, request-correlated reports explaining why
//! a request was accepted, rejected, or could not be decided.
//! Sensitive free-text fields (e.g. request labels) are only ever
//! emitted in redacted form.

use crate::syntax::{NodeId, Var};
use serde::{Deserialize, Serialize};

/// Failure / success category of a processed request.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "category", rename_all = "snake_case")]
pub enum Category {
    /// Circuit accepted; model count produced.
    Accepted,
    /// Malformed circuit description (syntax level).
    InvalidSyntax,
    /// AND node whose children share a variable.
    AndNotDecomposable,
    /// OR node with two children that can be true simultaneously.
    OrNotDeterministic,
    /// Determinism could not be established within configured limits.
    Undecidable,
    /// Declared universe is inconsistent (e.g. duplicates).
    BadUniverse,
    /// Core count disagreed with the independent brute-force recount.
    IndependentCheckMismatch,
}

/// One structured diagnostic line for a request.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Diagnostic {
    pub request_id: String,
    pub category: Category,
    /// Node the diagnostic refers to, when applicable.
    pub node: Option<NodeId>,
    /// Key state describing the decision (already redacted).
    pub detail: String,
    /// Redacted form of the request label, if one was supplied.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub label_redacted: Option<String>,
}

impl Diagnostic {
    pub fn new(request_id: &str, category: Category, node: Option<NodeId>, detail: String) -> Self {
        Diagnostic {
            request_id: request_id.to_string(),
            category,
            node,
            detail,
            label_redacted: None,
        }
    }

    pub fn with_label(mut self, label: Option<&str>) -> Self {
        self.label_redacted = label.map(redact);
        self
    }

    pub fn to_json(&self) -> String {
        serde_json::to_string(self).expect("diagnostic serialization cannot fail")
    }
}

/// Redact a sensitive string: keep only a stable short fingerprint.
/// Uses FNV-1a (std-only, deterministic) so logs can be correlated
/// without revealing the original text.
pub fn redact(s: &str) -> String {
    let mut h: u64 = 0xcbf29ce484222325;
    for b in s.as_bytes() {
        h ^= *b as u64;
        h = h.wrapping_mul(0x100000001b3);
    }
    format!("redacted:{h:016x}")
}

/// Human-readable rendering of a variable set for diagnostics.
pub fn vars_str(vars: &[Var]) -> String {
    let body: Vec<String> = vars.iter().map(|v| v.to_string()).collect();
    format!("{{{}}}", body.join(","))
}
