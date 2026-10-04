//! Shared test utilities: structured test logging and fixture loading.
//!
//! Every test case logs lines of the form:
//!   [rbp-test][run=<pid>-<ts>] case=<name> step=<n> <key=value>...
//!   [rbp-test][run=...] case=<name> verdict=PASS basis="<decision basis>"
//! so logs can be correlated with the input identity and run, and every
//! verdict records *why* it passed (per-value compare, exact error category,
//! located block index, ...).
//!
//! Each integration-test binary compiles this module independently; helpers
//! not used by a given binary are expected.
#![allow(dead_code)]

use std::sync::OnceLock;

/// Identity of one test-binary run (pid + start timestamp).
pub fn run_id() -> &'static str {
    static RUN_ID: OnceLock<String> = OnceLock::new();
    RUN_ID.get_or_init(|| {
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis())
            .unwrap_or(0);
        format!("{}-{}", std::process::id(), ts)
    })
    .as_str()
}

/// FNV-1a hash of a value column, used to bind logs to the exact input.
pub fn input_fingerprint(values: &[u64]) -> String {
    let mut h: u64 = 0xcbf29ce484222325;
    for v in values {
        for b in v.to_le_bytes() {
            h ^= b as u64;
            h = h.wrapping_mul(0x100000001b3);
        }
    }
    format!("{h:016x}")
}

pub struct TestLog {
    case: String,
    step: u64,
}

impl TestLog {
    pub fn new(case: &str) -> Self {
        let log = TestLog {
            case: case.to_string(),
            step: 0,
        };
        println!(
            "[rbp-test][run={}] case={} crate={} format={} event=begin",
            run_id(),
            case,
            env!("CARGO_PKG_VERSION"),
            rbp_column::format::FORMAT_VERSION
        );
        log
    }

    pub fn step(&mut self, msg: &str) {
        self.step += 1;
        println!(
            "[rbp-test][run={}] case={} step={} {}",
            run_id(),
            self.case,
            self.step,
            msg
        );
    }

    pub fn pass(self, basis: &str) {
        println!(
            "[rbp-test][run={}] case={} verdict=PASS basis=\"{}\"",
            run_id(),
            self.case,
            basis
        );
    }
}

/// A golden fixture case from fixtures/manifest.json.
#[derive(serde::Deserialize)]
pub struct FixtureCase {
    pub name: String,
    pub description: String,
    pub value_count: usize,
    /// Compact [[value, count], ...] run spec; expand to the full column.
    pub runs: Vec<(u64, u64)>,
    pub hex: String,
}

#[derive(serde::Deserialize)]
pub struct FixtureManifest {
    pub format: String,
    pub format_version: u8,
    pub generator: String,
    pub rle_min_run: usize,
    pub cases: Vec<FixtureCase>,
}

impl FixtureCase {
    pub fn expand_values(&self) -> Vec<u64> {
        let mut out = Vec::with_capacity(self.value_count);
        for &(value, count) in &self.runs {
            out.resize(out.len() + count as usize, value);
        }
        assert_eq!(
            out.len(),
            self.value_count,
            "fixture {}: runs do not expand to value_count",
            self.name
        );
        out
    }
}

pub fn load_manifest() -> FixtureManifest {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/fixtures/manifest.json");
    let text = std::fs::read_to_string(path)
        .unwrap_or_else(|e| panic!("cannot read fixture manifest {path}: {e}"));
    serde_json::from_str(&text)
        .unwrap_or_else(|e| panic!("cannot parse fixture manifest {path}: {e}"))
}
