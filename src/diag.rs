//! Diagnostic records: every accept / reject / indeterminate decision the
//! engine makes is recorded with a request id and the key state that drove
//! the decision. Sensitive content (process command names) is redacted to a
//! stable hash before it ever reaches a record or a log line.

use crate::model::{Pid, Seq};
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;

/// Outcome of a decision the engine was asked to make.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Decision {    /// The input was accepted and processed.
    Accepted,
    /// The input was rejected (e.g. out-of-order snapshot).
    Rejected,
    /// The engine could not determine a definite answer for this interval or
    /// event; the reason field explains why.
    Indeterminate,
}

/// One diagnostic record.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DiagRecord {
    /// Request / record identifier, unique per ingest request.
    pub request_id: String,
    /// Snapshot sequence this record relates to, if any.
    pub seq: Option<Seq>,
    pub decision: Decision,
    /// Short action label: "ingest", "delta", "exit", "reparent", ...
    pub action: String,
    pub pid: Option<Pid>,
    /// Human-readable reason; contains no sensitive data.
    pub reason: String,
    /// Key state that drove the decision (redacted).
    pub key_state: serde_json::Value,
}

/// Append a record to a bounded ring buffer.
pub fn push_record(buf: &mut VecDeque<DiagRecord>, capacity: usize, record: DiagRecord) {
    buf.push_back(record);
    while buf.len() > capacity {
        buf.pop_front();
    }
}

/// Redact a process command name to a stable, non-reversible token.
/// FNV-1a 64-bit, first 12 hex chars — enough to correlate records about the
/// same binary without disclosing its name.
pub fn redact_comm(comm: &str) -> String {
    const FNV_OFFSET: u64 = 0xcbf2_9ce4_8422_2325;
    const FNV_PRIME: u64 = 0x0000_0100_0000_01b3;
    let mut hash = FNV_OFFSET;
    for byte in comm.as_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(FNV_PRIME);
    }
    format!("comm#{hash:012x}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redaction_is_stable_and_hides_name() {
        let a = redact_comm("worker");
        let b = redact_comm("worker");
        assert_eq!(a, b);
        assert!(!a.contains("worker"));
        assert!(a.starts_with("comm#"));
        assert_ne!(redact_comm("worker"), redact_comm("init"));
    }

    #[test]
    fn ring_buffer_evicts_oldest() {
        let mut buf = VecDeque::new();
        for i in 0..5 {
            push_record(
                &mut buf,
                3,
                DiagRecord {
                    request_id: format!("req-{i}"),
                    seq: None,
                    decision: Decision::Accepted,
                    action: "test".into(),
                    pid: None,
                    reason: "r".into(),
                    key_state: serde_json::Value::Null,
                },
            );
        }
        assert_eq!(buf.len(), 3);
        assert_eq!(buf.front().unwrap().request_id, "req-2");
    }
}
