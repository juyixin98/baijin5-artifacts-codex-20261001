//! Audit records. Deliberately redacted: a record carries the session id,
//! the request id, an event name, an outcome, and a detail string that may
//! contain counts and a truncated payload digest — never raw elements and
//! never full point lists.

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct AuditRecord {
    pub id: i64,
    pub ts_unix: i64,
    pub session_id: Option<String>,
    pub request_id: String,
    pub event: String,
    /// "accepted" | "rejected" | "undetermined"
    pub outcome: String,
    pub detail: String,
}

/// Truncated SHA-256 digest of a point payload, for correlating audit rows
/// with submissions without storing the points themselves.
pub fn payload_digest(points: &[[u8; 32]]) -> String {
    let mut hasher = Sha256::new();
    for p in points {
        hasher.update(p);
    }
    let digest = hex::encode(hasher.finalize());
    digest[..16].to_string()
}

pub fn now_unix() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}
