//! Per-run structured logging so outputs can be correlated with inputs and a
//! run identity: timestamped run id, input digest, versions, verdict and the
//! step-level computation trace. Records are appended as JSON Lines.

use serde::Serialize;
use std::fs::{create_dir_all, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

pub const SERVICE_VERSION: &str = env!("CARGO_PKG_VERSION");
pub const RUSTC_VERSION: &str = "rustc 1.98 (build toolchain)";

static SEQ: AtomicU64 = AtomicU64::new(0);

pub fn new_run_id() -> String {
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    let seq = SEQ.fetch_add(1, Ordering::Relaxed);
    format!("run-{millis:016x}-{seq:04x}")
}

/// Small deterministic FNV-1a 64-bit digest of the raw input bytes, rendered
/// hex. Correlates log lines with the exact request body.
pub fn digest_hex(bytes: &[u8]) -> String {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in bytes {
        hash ^= *byte as u64;
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    format!("{hash:016x}")
}

#[derive(Debug, Clone, Serialize)]
pub struct RunRecord<'a> {
    pub run_id: &'a str,
    pub ts_unix_ms: u128,
    pub service_version: &'static str,
    pub toolchain: &'static str,
    pub endpoint: &'a str,
    pub input_digest: &'a str,
    pub verdict: &'a str,
    pub category: &'a str,
    pub steps_used: Option<usize>,
    pub budget: Option<usize>,
    pub detail: &'a str,
}

pub struct RunLogger {
    dir: PathBuf,
}

impl RunLogger {
    pub fn new(dir: impl Into<PathBuf>) -> Self {
        RunLogger { dir: dir.into() }
    }

    pub fn record(&self, record: &RunRecord<'_>) {
        if let Err(e) = self.try_record(record) {
            eprintln!("[runlog] failed to persist record: {e}");
        }
    }

    fn try_record(&self, record: &RunRecord<'_>) -> std::io::Result<()> {
        create_dir_all(&self.dir)?;
        let path = self.dir.join("runs.jsonl");
        let mut file = OpenOptions::new().create(true).append(true).open(path)?;
        let line = serde_json::to_string(record).expect("record serializes");
        writeln!(file, "{line}")
    }
}

pub fn now_millis() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0)
}
