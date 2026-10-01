//! Replayable execution traces.
//!
//! Every join run — success, truncation, or failure — records a
//! [`RunRecord`] keyed by a unique, time-sortable **run id**. Records
//! contain enough information to reproduce a problem offline:
//!
//! * the exact plan, budget and input fingerprints/sizes,
//! * key intermediate state (permutation boundaries, gate positions,
//!   bitmap popcounts, predicate-2 bounds),
//! * the decision at each interesting step and *why* it was taken
//!   (e.g. "strict boundary stops before equal group key=2"),
//! * the categorized outcome and counters.
//!
//! Records are JSON-serializable and written to a local trace directory
//! (one file per run) by the CLI/API; `iejoin replay <file>` rebuilds
//! the inputs from an embedded fixture (tests) or path and reruns,
//! asserting the recorded outcome is reproduced.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

use crate::error::JoinError;
use crate::operator::bitmap::Counters;
use crate::resource::Budget;

/// One intermediate-state observation captured during enumeration.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct TraceEvent {
    /// Monotonic step number within the run.
    pub step: u64,
    /// Machine-readable event kind (`right_row`, `gate_advance`,
    /// `boundary`, `emit`, `truncate`, ...).
    pub kind: String,
    /// Human- and diff-readable explanation / decision rationale.
    pub rationale: String,
    /// Structured intermediate state (positions, bounds, counts).
    pub state: serde_json::Value,
}

/// Final outcome classification mirrored from [`crate::error`].
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Outcome {
    Completed,
    Truncated,
    Failed,
}

/// The full record of one run.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RunRecord {
    /// Unique run id, e.g. `run-20260927T131315Z-000001`.
    pub run_id: String,
    pub started_unix_ms: u128,
    pub outcome: Outcome,
    /// Error category/code when outcome == failed.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<ErrorSnapshot>,
    pub budget: Budget,
    pub input_summary: serde_json::Value,
    pub events: Vec<TraceEvent>,
    pub counters: Counters,
    pub pairs_emitted: u64,
}

/// Serializable projection of [`JoinError`].
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ErrorSnapshot {
    pub category: String,
    pub code: String,
    pub message: String,
    pub details: serde_json::Value,
}

impl From<&JoinError> for ErrorSnapshot {
    fn from(e: &JoinError) -> Self {
        Self {
            category: e.category.to_string(),
            code: e.code.to_string(),
            message: e.message.clone(),
            details: serde_json::Value::Object(e.details.clone()),
        }
    }
}

/// Thread-safe run-id generator and in-memory trace sink.
pub struct Tracer {
    dir: Option<PathBuf>,
    seq: Mutex<u64>,
}

impl Tracer {
    /// Tracer that also persists each record as JSON under `dir`.
    #[must_use]
    pub fn new(dir: Option<PathBuf>) -> Self {
        Self {
            dir,
            seq: Mutex::new(0),
        }
    }

    /// Mint a unique, time-sortable run id.
    #[must_use]
    pub fn new_run_id(&self) -> String {
        let mut seq = self.seq.lock().expect("tracer lock poisoned");
        *seq += 1;
        let unix_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0, |d| d.as_millis());
        format!("run-{unix_ms:013}-{seq:06}")
    }

    /// Persist a finished record to the trace directory (when set) and
    /// return its path.
    ///
    /// # Errors
    /// Returns the I/O error as a `Compute` failure if the directory is
    /// configured but unwritable.
    pub fn persist(&self, record: &RunRecord) -> Result<Option<PathBuf>, JoinError> {
        let Some(dir) = &self.dir else {
            return Ok(None);
        };
        fs::create_dir_all(dir).map_err(|e| {
            JoinError::compute(
                "trace_dir_unavailable",
                format!("cannot create trace dir {}: {e}", dir.display()),
            )
        })?;
        let path = dir.join(format!("{}.json", record.run_id));
        let json = serde_json::to_string_pretty(record).map_err(|e| {
            JoinError::compute(
                "trace_serialize_failed",
                format!("cannot encode trace: {e}"),
            )
        })?;
        fs::write(&path, json).map_err(|e| {
            JoinError::compute(
                "trace_write_failed",
                format!("cannot write trace {}: {e}", path.display()),
            )
        })?;
        Ok(Some(path))
    }
}

/// Load a record previously written by [`Tracer::persist`].
///
/// # Errors
/// `Compute` for missing/unreadable/malformed trace files.
pub fn load(path: &Path) -> Result<RunRecord, JoinError> {
    let bytes = fs::read(path).map_err(|e| {
        JoinError::compute(
            "trace_read_failed",
            format!("cannot read trace {}: {e}", path.display()),
        )
    })?;
    serde_json::from_slice(&bytes).map_err(|e| {
        JoinError::compute(
            "trace_parse_failed",
            format!("malformed trace {}: {e}", path.display()),
        )
    })
}
