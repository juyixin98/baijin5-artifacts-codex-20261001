//! Run-id allocation.
//!
//! Every request gets a `run-<startup_epoch>-<seq>` id that appears in
//! the response body and in every log line for that request, so a failing
//! run can be replayed from the logs: input summary, intermediate peel
//! counts, and the final decision are all tagged with it.

use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Debug)]
pub struct RunIds {
    instance: u64,
    seq: AtomicU64,
}

impl Default for RunIds {
    fn default() -> Self {
        Self::new()
    }
}

impl RunIds {
    pub fn new() -> Self {
        let instance = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        RunIds { instance, seq: AtomicU64::new(0) }
    }

    pub fn next(&self) -> String {
        let n = self.seq.fetch_add(1, Ordering::Relaxed);
        format!("run-{}-{n:06}", self.instance)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids_are_unique_and_monotonic() {
        let g = RunIds::new();
        let a = g.next();
        let b = g.next();
        assert_ne!(a, b);
        assert!(a.starts_with("run-"));
        assert!(b > a, "sequence numbers should increase: {a} vs {b}");
    }
}
