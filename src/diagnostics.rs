//! Diagnostics: every request ends in a structured decision record saying
//! *why* it was accepted, rejected, or could not be decided, carrying the
//! request id and the key state that drove the decision.
//!
//! Redaction rule: request/response *content* is never logged. Only lengths,
//! chunk counts, and truncated digest prefixes (first 8 hex chars) appear in
//! records — enough to correlate, never enough to reconstruct data.

use std::collections::BTreeMap;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DecisionKind {
    Accept,
    Reject,
    /// The service cannot decide (e.g. corrupt or inconsistent input that is
    /// neither verifiably good nor provably malicious).
    Undetermined,
}

impl DecisionKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            DecisionKind::Accept => "accept",
            DecisionKind::Reject => "reject",
            DecisionKind::Undetermined => "undetermined",
        }
    }
}

/// Truncate a hex digest for logs: first 8 chars plus an ellipsis marker.
pub fn short_digest(hex_digest: &str) -> String {
    let prefix: String = hex_digest.chars().take(8).collect();
    format!("{prefix}…")
}

/// Emit a structured decision record. `state` holds the key facts that drove
/// the decision (lengths, counts, categories) — already redacted by callers.
pub fn record(
    request_id: &str,
    endpoint: &str,
    kind: DecisionKind,
    reason: &str,
    state: BTreeMap<String, String>,
) {
    tracing::info!(
        request_id = %request_id,
        endpoint = %endpoint,
        decision = kind.as_str(),
        reason = %reason,
        state = ?state,
        "request decision"
    );
}

/// Convenience: one-fact state map.
pub fn state_of(pairs: &[(&str, String)]) -> BTreeMap<String, String> {
    pairs.iter().map(|(k, v)| (k.to_string(), v.clone())).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn digest_is_truncated() {
        let d = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
        assert_eq!(short_digest(d), "ba7816bf…");
        // Never leaks the full digest.
        assert!(!short_digest(d).contains("8f01"));
    }

    #[test]
    fn decision_kinds_have_stable_names() {
        assert_eq!(DecisionKind::Accept.as_str(), "accept");
        assert_eq!(DecisionKind::Reject.as_str(), "reject");
        assert_eq!(DecisionKind::Undetermined.as_str(), "undetermined");
    }
}
