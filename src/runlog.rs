//! Replayable run logging.
//!
//! Every execution owns a [`RunLog`] keyed by a human-replayable run id of
//! `run-<utc timestamp>-<short random>`. Events record the decision points
//! needed to reproduce a failure offline:
//! * input boundaries (file, rows, batches),
//! * partition/spill decisions (level, partition, resident bytes, rows),
//! * count arithmetic for ALL operations (before/after multiplicities),
//! * the terminal verdict with its error category.
//!
//! The JSONL form is written next to spill output and is stable field-for-field
//! enough to diff between an in-memory and an external-memory run of the same
//! query — which is exactly how tests prove the two agree.

use std::fs::File;
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::Serialize;

use crate::error::SetOpsError;

/// Generate a replayable run id.
pub fn new_run_id() -> String {
    use std::sync::atomic::AtomicU32;
    static COUNTER: AtomicU32 = AtomicU32::new(0);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let seq = COUNTER.fetch_add(1, Ordering::Relaxed);
    format!(
        "run-{}-{:04x}-{:06x}",
        nanos.as_secs(),
        seq,
        (nanos.subsec_nanos() as u64 ^ std::process::id() as u64) & 0xff_ffff
    )
}

#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Side {
    Left,
    Right,
}

impl Side {
    pub fn label(self) -> &'static str {
        match self {
            Self::Left => "L",
            Self::Right => "R",
        }
    }
}

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "event", rename_all = "snake_case")]
pub enum RunEvent {
    QueryStart {
        op: String,
        qualifier: String,
        mode: String,
        left_rows: usize,
        right_rows: usize,
        columns: usize,
    },
    InputRead {
        side: String,
        source: String,
        batches: usize,
        rows: usize,
    },
    PartitionAssigned {
        side: String,
        level: usize,
        partition: usize,
        rows: usize,
        hash_bucket: usize,
    },
    MemoryCheck {
        level: usize,
        partition: String,
        resident_bytes: usize,
        budget_bytes: usize,
        decision: &'static str,
    },
    Spill {
        side: String,
        level: usize,
        partition: usize,
        rows: usize,
        bytes: u64,
        path: String,
    },
    SpillRead {
        side: String,
        level: usize,
        partition: usize,
        rows: usize,
    },
    /// Exact multiplicity arithmetic that produced an ALL result row.
    CountArithmetic {
        op: String,
        key_hex_prefix: String,
        left_count: u64,
        right_count: u64,
        result_count: u64,
        reason: String,
    },
    PartitionRecursed {
        level: usize,
        partition: usize,
        reason: String,
        fanout: usize,
    },
    OutputBatch {
        rows: usize,
        running_total: u64,
        distinct_keys: usize,
    },
    Verdict {
        status: &'static str,
        error_kind: Option<String>,
        error_code: Option<String>,
        message: String,
    },
}

#[derive(Debug, Serialize)]
struct Envelope {
    run_id: String,
    seq: u64,
    #[serde(flatten)]
    event: RunEvent,
}

pub struct RunLog {
    run_id: String,
    seq: AtomicU64,
    sink: Option<File>,
    /// In-memory mirror so callers (tests, /runlog endpoint) can inspect
    /// without re-reading disk.
    events: Vec<RunEvent>,
}

impl RunLog {
    pub fn new(run_id: impl Into<String>) -> Self {
        Self {
            run_id: run_id.into(),
            seq: AtomicU64::new(0),
            sink: None,
            events: Vec::new(),
        }
    }

    /// Also persist JSONL to `dir/<run_id>.jsonl`.
    pub fn with_file(dir: impl Into<PathBuf>, run_id: String) -> Result<Self, SetOpsError> {
        let dir = dir.into();
        std::fs::create_dir_all(&dir).map_err(|e| {
            SetOpsError::resource(
                crate::error::ResourceCode::SpillIo,
                format!("cannot create runlog dir {}: {e}", dir.display()),
            )
        })?;
        let path = dir.join(format!("{run_id}.jsonl"));
        let file = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
            .map_err(|e| {
                SetOpsError::state(
                    crate::error::StateCode::RunIdConflict,
                    format!("run log {} already exists: {e}", path.display()),
                )
            })?;
        Ok(Self {
            run_id,
            seq: AtomicU64::new(0),
            sink: Some(file),
            events: Vec::new(),
        })
    }

    pub fn run_id(&self) -> &str {
        &self.run_id
    }

    pub fn record(&mut self, event: RunEvent) {
        let seq = self.seq.fetch_add(1, Ordering::Relaxed);
        let env = Envelope {
            run_id: self.run_id.clone(),
            seq,
            event: event.clone(),
        };
        if let Some(f) = self.sink.as_mut() {
            let line = serde_json::to_string(&env).expect("run event serializes");
            if let Err(e) = writeln!(f, "{line}").and_then(|_| f.flush()) {
                // Degrade to in-memory only; mark by swallowing — the run must
                // not fail because observability IO failed. Keep the detail in
                // stderr for replay forensics.
                eprintln!("[{}] runlog write failed: {e}", self.run_id);
            }
        }
        self.events.push(event);
    }

    pub fn events(&self) -> &[RunEvent] {
        &self.events
    }

    /// Compact replay summary: the decisions a human needs to reproduce.
    pub fn replay_summary(&self) -> ReplaySummary {
        let mut spills = 0usize;
        let mut spill_rows = 0u64;
        let mut recursions = 0usize;
        let mut output_rows = 0u64;
        let mut max_resident = 0usize;
        let mut arithmetic = 0usize;
        for e in &self.events {
            match e {
                RunEvent::Spill { rows, .. } => {
                    spills += 1;
                    spill_rows += *rows as u64;
                }
                RunEvent::PartitionRecursed { .. } => recursions += 1,
                RunEvent::OutputBatch { running_total, .. } => output_rows = *running_total,
                RunEvent::MemoryCheck { resident_bytes, .. } => {
                    max_resident = max_resident.max(*resident_bytes)
                }
                RunEvent::CountArithmetic { .. } => arithmetic += 1,
                _ => {}
            }
        }
        ReplaySummary {
            run_id: self.run_id.clone(),
            events: self.events.len(),
            spills,
            spill_rows,
            recursions,
            output_rows,
            max_resident_bytes: max_resident,
            count_arithmetic_events: arithmetic,
        }
    }
}

#[derive(Debug, Serialize)]
pub struct ReplaySummary {
    pub run_id: String,
    pub events: usize,
    pub spills: usize,
    pub spill_rows: u64,
    pub recursions: usize,
    pub output_rows: u64,
    pub max_resident_bytes: usize,
    pub count_arithmetic_events: usize,
}
