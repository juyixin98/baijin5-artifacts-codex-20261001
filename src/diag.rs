//! Diagnostic events: every decision the runtime makes is recorded with the
//! request/record identity, the key state, and *why* it was accepted,
//! rejected, or could not be decided. Sensitive data (paths) is redacted
//! before it ever reaches an event string.

use serde::Serialize;

use crate::model::{IoOp, UserData};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Decision {
    /// Request accepted / event is a definitive positive outcome.
    Accept,
    /// Request rejected; the reason says why.
    Reject,
    /// The runtime cannot decide (e.g. a timeout: the IO may or may not
    /// have happened; resource backpressure: retry may succeed later).
    Indeterminate,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DiagKind {
    ConnectionOpened,
    ConnectionCloseRequested,
    ConnectionClosed,
    SubmissionAccepted,
    SubmissionRejected,
    Dispatch,
    DispatchDeferred,
    CancelAccepted,
    CancelRejected,
    CancelForwarded,
    CompletionFinalized,
    OrphanCompletion,
    Timeout,
    BufferReleased,
    BufferReleaseRejected,
}

#[derive(Debug, Clone, Serialize)]
pub struct DiagEvent {
    pub seq: u64,
    pub ts_ms: u64,
    /// External request identifier supplied by the caller, if any.
    pub request_id: Option<String>,
    /// The submission token this event is about, if any.
    pub subject: Option<UserData>,
    pub kind: DiagKind,
    pub decision: Decision,
    /// Human-readable justification. Already redacted.
    pub reason: String,
}

/// FNV-1a 32-bit hash, used to fingerprint paths without revealing them.
fn fnv1a(bytes: &[u8]) -> u32 {
    let mut h: u32 = 0x811c_9dc5;
    for b in bytes {
        h ^= u32::from(*b);
        h = h.wrapping_mul(0x0100_0193);
    }
    h
}

/// Redact a filesystem path: only a stable hash and the length survive.
/// The original path never appears in diagnostics.
pub fn redact_path(path: &str) -> String {
    format!("<path#{:08x}:len{}>", fnv1a(path.as_bytes()), path.len())
}

/// Redacted one-line description of an operation for diagnostics.
pub fn redact_op(op: &IoOp) -> String {
    match op {
        IoOp::Read { path } => format!("read {}", redact_path(path)),
        IoOp::Write { path, len } => format!("write {} ({len}B)", redact_path(path)),
    }
}
