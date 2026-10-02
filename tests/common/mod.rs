//! Shared helpers for integration tests.
//!
//! Expected values in these tests are hand-computed from the fixture files
//! (see tests/fixtures/), not produced by the implementation under test.

// Each integration test binary compiles this module independently and uses
// only a subset of the helpers.
#![allow(dead_code)]

use procdiff::config::Config;
use procdiff::delta::IntervalDelta;
use procdiff::engine::Engine;
use procdiff::model::{Pid, ProcessIdentity};
use procdiff::snapshot;
use std::path::PathBuf;

pub const BOOT: &str = "boot-aaa111";

pub fn fixtures_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

pub fn test_config() -> Config {
    Config {
        listen: "127.0.0.1:0".to_string(),
        data_dir: PathBuf::from("/tmp/procdiff-test-unused"),
        hz: 100,
        counter_modulus: 1u64 << 32,
        max_jiffies_per_interval: 200,
        max_diag_records: 256,
    }
}

pub fn id(pid: Pid, start_time: u64) -> ProcessIdentity {
    ProcessIdentity {
        boot_id: BOOT.to_string(),
        pid,
        start_time,
    }
}

/// Ingest every snapshot of a fixture case, in sequence order.
pub fn ingest_case(case: &str) -> Engine {
    let root = fixtures_dir().join(case);
    let mut engine = Engine::new(test_config());
    for (_, path) in snapshot::list_snapshots(&root).expect("list snapshots") {
        let snap = snapshot::load_snapshot(&path).expect("load snapshot");
        let report = engine.ingest(&snap);
        assert!(!report.rejected, "snapshot {:?} rejected", path);
    }
    engine
}

pub fn deltas_for(engine: &Engine, pid: Pid) -> Vec<&IntervalDelta> {
    engine
        .state
        .deltas
        .iter()
        .filter(|d| d.identity.pid == pid)
        .collect()
}
