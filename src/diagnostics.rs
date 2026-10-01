//! Structured diagnostics.
//!
//! Every log record carries the request id and key resource state. Sensitive
//! string values are never logged — only redacted fingerprints (`len + hash`).

use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};

use tracing::{event, Level};

use crate::resources::MemoryGuard;

/// Per-request correlation id; returned in the `x-request-id` response header.
#[derive(Debug, Clone)]
pub struct RequestId(pub String);

impl RequestId {
    pub fn new() -> Self {
        // 16 hex chars from a process-local hasher is sufficient for local
        // synthetic workloads; no external id service involved.
        let mut h = DefaultHasher::new();
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
            .hash(&mut h);
        std::process::id().hash(&mut h);
        use std::cell::Cell;
        thread_local! { static SEQ: Cell<u64> = const { Cell::new(0) }; }
        SEQ.with(|s| {
            let n = s.get().wrapping_add(1);
            n.hash(&mut h);
        });
        RequestId(format!("{:016x}", h.finish()))
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

/// Redact a possibly sensitive string value into a stable fingerprint.
/// Only length and a non-reversible hash are recorded, never the payload.
pub fn redact(s: &str) -> String {
    let mut h = DefaultHasher::new();
    s.hash(&mut h);
    format!(
        "<str len={} fp={:08x}>",
        s.chars().count(),
        h.finish() as u32
    )
}

#[derive(Debug, Clone, Copy)]
pub struct BudgetSnapshot {
    pub used: usize,
    pub high_water: usize,
    pub limit: usize,
}

impl BudgetSnapshot {
    pub fn of(g: &MemoryGuard) -> Self {
        Self {
            used: g.used(),
            high_water: g.high_water(),
            limit: g.limit(),
        }
    }
}

pub fn log_accept(rid: &RequestId, what: &str, detail: &str) {
    event!(
        target: "pctl::diag",
        Level::INFO,
        request_id = %rid,
        decision = "accept",
        stage = what,
        %detail,
        "accepted"
    );
}

pub fn log_reject(rid: &RequestId, what: &str, code: &str, detail: &str) {
    event!(
        target: "pctl::diag",
        Level::WARN,
        request_id = %rid,
        decision = "reject",
        stage = what,
        code,
        %detail,
        "rejected"
    );
}

pub fn log_indeterminate(rid: &RequestId, what: &str, code: &str, detail: &str) {
    event!(
        target: "pctl::diag",
        Level::WARN,
        request_id = %rid,
        decision = "indeterminate",
        stage = what,
        code,
        %detail,
        "result cannot be decided"
    );
}

pub fn log_resource(rid: &RequestId, what: &str, b: BudgetSnapshot, detail: &str) {
    event!(
        target: "pctl::diag",
        Level::INFO,
        request_id = %rid,
        stage = what,
        budget_used = b.used,
        budget_high_water = b.high_water,
        budget_limit = b.limit,
        %detail,
        "resource state"
    );
}

#[cfg(test)]
mod tests {
    use super::redact;

    #[test]
    fn redact_never_contains_payload_and_is_stable() {
        let secret = "patient-secret-value-123";
        let r = redact(secret);
        assert!(
            !r.contains(secret),
            "fingerprint must not contain payload: {r}"
        );
        assert!(!r.contains("patient"));
        // stable for equal input, divergent for distinct input
        assert_eq!(redact(secret), r);
        assert_ne!(redact("a-different-string"), r);
        // length is reported (char count), content is not
        assert!(r.contains(&format!("len={}", secret.chars().count())));
    }

    #[test]
    fn request_ids_are_distinct() {
        assert_ne!(super::RequestId::new().0, super::RequestId::new().0);
    }
}
