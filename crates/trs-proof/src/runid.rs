//! Deterministic run identifiers.
//!
//! The run id is a content hash of the exact inputs (system + certificate),
//! so the same inputs always yield the same id and any logged run can be
//! replayed from the recorded artifacts. It identifies a *run*, it is not a
//! cryptographic integrity proof.

use trs_syntax::{Certificate, System};

const FNV_OFFSET: u64 = 0xcbf2_9ce4_8422_2325;
const FNV_PRIME: u64 = 0x0000_0100_0000_01b3;

pub fn fnv1a64(bytes: &[u8]) -> u64 {
    let mut hash = FNV_OFFSET;
    for byte in bytes {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(FNV_PRIME);
    }
    hash
}

/// `run-<16 hex digits>` over the canonical JSON of both inputs.
pub fn compute_run_id(system: &System, cert: &Certificate) -> String {
    let mut bytes = serde_json::to_vec(system).expect("system serialization is infallible");
    bytes.push(0);
    bytes.extend(serde_json::to_vec(cert).expect("certificate serialization is infallible"));
    format!("run-{:016x}", fnv1a64(&bytes))
}
