//! Shared test fixtures: a structured `TestRun` logger and independent
//! oracle helpers.
//!
//! Oracle rule: expected values are computed here with plain `Vec<u8>`
//! operations — never by calling into the model under test — so the
//! reference answers are independent of the implementation.

// Each integration-test binary compiles this module independently and may
// not use every helper.
#![allow(dead_code)]

use std::fmt::Debug;
use std::sync::Once;

pub const PAGE: u64 = 512;

static INIT: Once = Once::new();

/// Structured per-test logger. Every line carries the test name, a run id,
/// the crate version and a step counter, so output can be correlated back
/// to the input/run that produced it.
pub struct TestRun {
    pub test: String,
    pub run_id: String,
    step: usize,
}

impl TestRun {
    pub fn new(test: &str) -> Self {
        INIT.call_once(mmap_model::telemetry::init_tracing);
        let run_id = mmap_model::telemetry::run_id();
        let tr = TestRun {
            test: test.to_string(),
            run_id,
            step: 0,
        };
        tr.note(
            "start",
            &format!(
                "version={} page_size={PAGE}",
                mmap_model::telemetry::crate_version()
            ),
        );
        tr
    }

    /// Log a progress step with its inputs.
    pub fn step(&mut self, what: &str, detail: &str) {
        self.step += 1;
        self.note(what, detail);
    }

    /// Log a decision point: expected vs actual, then assert equality.
    pub fn check<E, A>(&self, what: &str, expected: &E, actual: &A)
    where
        E: ?Sized + Debug + PartialEq<A>,
        A: ?Sized + Debug,
    {
        let verdict = if expected == actual {
            "MATCH"
        } else {
            "MISMATCH"
        };
        self.note(
            "check",
            &format!("{what}: expected={expected:?} actual={actual:?} -> {verdict}"),
        );
        assert_eq!(expected, actual, "{what} ({verdict})");
    }

    fn note(&self, what: &str, detail: &str) {
        let line = format!(
            "[{}][{}][step={:02}] {}: {}",
            self.test, self.run_id, self.step, what, detail
        );
        eprintln!("{line}");
        tracing::info!(test = %self.test, run_id = %self.run_id, step = self.step, "{what}: {detail}");
    }
}

/// Build a fresh model over a volatile store with the test page size.
pub fn test_vm() -> mmap_model::model::Vm<mmap_model::store::MemStore> {
    let cfg = mmap_model::config::ModelConfig {
        page_size: PAGE,
        max_file_size: 1 << 20,
        max_mappings: 64,
    };
    mmap_model::model::Vm::new(cfg, mmap_model::store::MemStore::new())
}

/// Fill `buf[off..off+len]` with a repeated byte (oracle write).
pub fn oracle_fill(buf: &mut [u8], off: usize, byte: u8, len: usize) {
    buf[off..off + len].fill(byte);
}

/// A whole page filled with one byte (oracle page).
pub fn page_of(byte: u8) -> Vec<u8> {
    vec![byte; PAGE as usize]
}
