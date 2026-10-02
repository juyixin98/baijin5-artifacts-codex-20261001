//! Structured event log.
//!
//! Every state-changing operation appends one JSON line to
//! `<data_dir>/log/events-<run_id>.jsonl` and keeps the tail in a memory ring
//! for the diagnostics endpoint. Events carry the run id, a monotonic
//! sequence number, the operation, key intermediate state (object counts,
//! per-page copy decisions) and — for failures — the error category and the
//! reason, so a problem can be replayed from the log alone.

use std::collections::VecDeque;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::error::{Result, ServiceError};

const RING_CAPACITY: usize = 256;

#[derive(Debug, Clone, Serialize)]
pub struct Event {
    pub seq: u64,
    pub ts: String,
    pub run_id: String,
    pub op: String,
    /// "ok" | "error"
    pub result: &'static str,
    /// Error category when result == "error".
    #[serde(skip_serializing_if = "Option::is_none")]
    pub category: Option<String>,
    /// Human/operator-facing reason or decision rationale.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    /// Operation-specific structured detail (intermediate state, decisions).
    pub detail: serde_json::Value,
}

pub struct EventLog {
    run_id: String,
    seq: u64,
    path: PathBuf,
    ring: VecDeque<Event>,
}

impl EventLog {
    /// Create a log for this run. `log_dir` is created if missing.
    pub fn new(log_dir: &Path, run_id: &str) -> Result<Self> {
        fs::create_dir_all(log_dir)
            .map_err(|e| ServiceError::internal("io", format!("create log dir: {e}")))?;
        let path = log_dir.join(format!("events-{run_id}.jsonl"));
        // Fail fast if the log file is not writable: diagnostics are part of
        // the contract, so a service that cannot log refuses to start.
        OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
            .map_err(|e| ServiceError::internal("io", format!("open event log: {e}")))?;
        Ok(Self {
            run_id: run_id.to_string(),
            seq: 0,
            path,
            ring: VecDeque::new(),
        })
    }

    pub fn run_id(&self) -> &str {
        &self.run_id
    }

    pub fn record_ok(&mut self, op: &str, detail: serde_json::Value) {
        self.append(op, "ok", None, None, detail);
    }

    pub fn record_err(&mut self, op: &str, err: &ServiceError, detail: serde_json::Value) {
        self.append(
            op,
            "error",
            Some(format!("{:?}", err.category).to_lowercase()),
            Some(format!("{}: {}", err.code, err.message)),
            detail,
        );
    }

    fn append(
        &mut self,
        op: &str,
        result: &'static str,
        category: Option<String>,
        reason: Option<String>,
        detail: serde_json::Value,
    ) {
        self.seq += 1;
        let event = Event {
            seq: self.seq,
            ts: now_rfc3339(),
            run_id: self.run_id.clone(),
            op: op.to_string(),
            result,
            category,
            reason,
            detail,
        };
        // Best-effort persistence: a logging failure must not corrupt the
        // data path, but it is surfaced on stderr so it is not silent.
        if let Ok(line) = serde_json::to_string(&event) {
            if let Ok(mut f) = OpenOptions::new().append(true).open(&self.path) {
                let _ = writeln!(f, "{line}");
            } else {
                eprintln!("eventlog: cannot append to {:?}: {}", self.path, line);
            }
        }
        if self.ring.len() == RING_CAPACITY {
            self.ring.pop_front();
        }
        self.ring.push_back(event);
    }

    pub fn recent(&self, limit: usize) -> Vec<Event> {
        self.ring.iter().rev().take(limit).cloned().collect()
    }
}

/// `run-20261002T201500Z-p12345-n6789` — timestamp + pid + nanos, unique
/// per process run even when several runs start within the same second.
pub fn new_run_id() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    format!(
        "run-{}-p{}-n{}",
        format_epoch_utc(now.as_secs()),
        std::process::id(),
        now.subsec_nanos()
    )
}

fn now_rfc3339() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    format_epoch_utc(secs)
}

/// Format epoch seconds as `YYYYMMDDTHHMMSSZ` (UTC) without external crates.
fn format_epoch_utc(secs: u64) -> String {
    let days = secs / 86_400;
    let rem = secs % 86_400;
    let (h, m, s) = (rem / 3600, (rem % 3600) / 60, rem % 60);
    // Civil date from day count (Howard Hinnant's algorithm).
    let z = days as i64 + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let mo = if mp < 10 { mp + 3 } else { mp - 9 };
    let yr = if mo <= 2 { y + 1 } else { y };
    format!("{yr:04}{mo:02}{d:02}T{h:02}{m:02}{s:02}Z")
}
