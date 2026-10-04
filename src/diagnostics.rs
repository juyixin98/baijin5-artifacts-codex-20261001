//! Diagnostics: request identity and decision logging.
//!
//! Every request gets an id (client-supplied `x-request-id` honored, else
//! generated). Each handled request ends in exactly one decision record
//! stating ACCEPT / REJECT / UNDETERMINED plus the reason and the key state
//! (sizes, chunk count, digest prefix). Payload bytes are NEVER logged —
//! only lengths and truncated digests, so logs are safe to collect.

use serde::Serialize;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

static REQUEST_COUNTER: AtomicU64 = AtomicU64::new(0);

/// Generate a request id that is unique within this process and sortable
/// enough to be useful: `req-<millis>-<counter>`.
pub fn new_request_id() -> String {
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    let n = REQUEST_COUNTER.fetch_add(1, Ordering::Relaxed);
    format!("req-{millis}-{n}")
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Decision {
    Accept,
    Reject,
    Undetermined,
}

/// One structured decision record. Emitted as a single tracing event.
#[derive(Debug, Serialize)]
pub struct DecisionRecord {
    pub request_id: String,
    pub operation: String,
    pub decision: Decision,
    pub reason: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub input_bytes: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub chunk_count: Option<usize>,
    /// First 16 hex chars of the payload digest — enough to correlate,
    /// useless for reconstructing content.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub payload_digest_prefix: Option<String>,
}

impl DecisionRecord {
    pub fn emit(self) {
        // Structured fields, one line, no payload content.
        tracing::info!(
            request_id = %self.request_id,
            operation = %self.operation,
            decision = ?self.decision,
            reason = %self.reason,
            input_bytes = ?self.input_bytes,
            chunk_count = ?self.chunk_count,
            payload_digest_prefix = ?self.payload_digest_prefix,
            "request decision"
        );
    }
}

/// Redacted digest helper: 8 bytes of hex, explicitly marked as truncated.
pub fn digest_prefix(hex_digest: &str) -> String {
    hex_digest.chars().take(16).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn request_ids_are_unique() {
        let a = new_request_id();
        let b = new_request_id();
        assert_ne!(a, b);
        assert!(a.starts_with("req-"));
    }

    #[test]
    fn digest_prefix_is_truncated() {
        let full = "0123456789abcdef0123456789abcdef";
        assert_eq!(digest_prefix(full), "0123456789abcdef");
    }
}
