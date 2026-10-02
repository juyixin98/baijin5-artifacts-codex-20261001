//! Replayable run log.
//!
//! Every execution writes an append-only JSON Lines record under
//! `<scratch>/runs/<run_id>/`. A record carries the run id, a monotonic step,
//! the event kind, a timestamp, and structured detail. The goal is that a
//! failing or successful run can be *replayed* from these records: the input
//! payloads are materialized on disk, and key intermediate state (fan-out,
//! partition/spill counts, recursive splits, output row count, verdict) is
//! captured together with the reason for each decision.

use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

use serde::Serialize;

use crate::error::{Result, SetOpError};
use crate::executor::ExecStats;

/// Lifecycle state of a run. Illegal transitions are `state_conflict`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RunState {
    Created,
    Running,
    Succeeded,
    Failed,
}

impl RunState {
    fn can_enter(self, next: RunState) -> bool {
        matches!(
            (self, next),
            (RunState::Created, RunState::Running)
                | (RunState::Running, RunState::Succeeded)
                | (RunState::Running, RunState::Failed)
        )
    }
}

/// Structured event written to the JSONL log.
#[derive(Debug, Clone, Serialize)]
pub struct RunEvent {
    pub run_id: String,
    pub step: u64,
    pub ts_unix_ms: u128,
    pub event: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub detail: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

/// One on-disk run: directory + ordered log + lifecycle guard.
pub struct RunRecord {
    pub run_id: String,
    pub dir: PathBuf,
    log_path: PathBuf,
    state: RunState,
    step: u64,
    // Serializes append within a process. Cross-process safety relies on the
    // fact a run_id is handled by one process in this service.
    write_lock: Mutex<()>,
}

impl RunRecord {
    pub fn create(root: &Path, run_id: &str) -> Result<Self> {
        let dir = root.join(run_id);
        if dir.exists() {
            return Err(SetOpError::state(
                "run_exists",
                format!("run_id {run_id} already exists; choose a unique id"),
            ));
        }
        fs::create_dir_all(&dir)
            .map_err(|e| SetOpError::compute("run_dir_create", e.to_string()))?;
        let log_path = dir.join("run.jsonl");
        let mut rec = Self {
            run_id: run_id.to_string(),
            dir,
            log_path,
            state: RunState::Created,
            step: 0,
            write_lock: Mutex::new(()),
        };
        rec.event("run_created", None, Some("new run directory allocated"))?;
        Ok(rec)
    }

    pub fn state(&self) -> RunState {
        self.state
    }

    /// Re-open a previously created run directory (e.g. after a one-shot
    /// execution) for read-oriented status/log access. The in-memory lifecycle
    /// guard is inferred from the log; the directory must already exist.
    pub fn open(root: &Path, run_id: &str) -> Result<Self> {
        let dir = root.join(run_id);
        if !dir.exists() {
            return Err(SetOpError::compute(
                "run_missing",
                format!("run directory for {run_id} does not exist"),
            ));
        }
        let log_path = dir.join("run.jsonl");
        let state = infer_state(&log_path);
        Ok(Self {
            run_id: run_id.to_string(),
            dir,
            log_path,
            state,
            step: 0,
            write_lock: Mutex::new(()),
        })
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }

    fn transition(&mut self, next: RunState) -> Result<()> {
        if !self.state.can_enter(next) {
            return Err(SetOpError::state(
                "illegal_transition",
                format!("run cannot move from {:?} to {:?}", self.state, next),
            ));
        }
        self.state = next;
        Ok(())
    }

    pub fn start(&mut self) -> Result<()> {
        self.transition(RunState::Running)?;
        self.event("run_started", None, Some("execution began"))
    }

    pub fn succeed(&mut self, stats: &ExecStats) -> Result<()> {
        self.transition(RunState::Succeeded)?;
        self.event(
            "run_succeeded",
            Some(serde_json::to_value(stats).unwrap_or(serde_json::Value::Null)),
            Some("all partitions drained; result fragments finalized"),
        )
    }

    pub fn fail(&mut self, code: &str, message: &str) -> Result<()> {
        // Failure from Created (input rejected before start) is also legal.
        if self.state == RunState::Created {
            self.state = RunState::Running;
        }
        self.transition(RunState::Failed)?;
        self.event(
            "run_failed",
            Some(serde_json::json!({ "code": code, "kind": error_kind_hint(code) })),
            Some(message),
        )
    }

    /// Append a structured event.
    pub fn event(
        &mut self,
        event: &str,
        detail: Option<serde_json::Value>,
        reason: Option<&str>,
    ) -> Result<()> {
        let _g = self.write_lock.lock().expect("run log lock poisoned");
        self.step += 1;
        let ev = RunEvent {
            run_id: self.run_id.clone(),
            step: self.step,
            ts_unix_ms: now_ms(),
            event: event.to_string(),
            detail,
            reason: reason.map(|s| s.to_string()),
        };
        let line = serde_json::to_vec(&ev)
            .map_err(|e| SetOpError::compute("log_serialize", e.to_string()))?;
        let mut f = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.log_path)
            .map_err(|e| SetOpError::compute("log_open", e.to_string()))?;
        f.write_all(&line)
            .and_then(|_| f.write_all(b"\n"))
            .and_then(|_| f.flush())
            .map_err(|e| SetOpError::compute("log_write", e.to_string()))
    }

    /// Persist the raw request body so a run is replayable.
    pub fn save_input(&mut self, name: &str, bytes: &[u8]) -> Result<PathBuf> {
        let path = self.dir.join(name);
        fs::write(&path, bytes).map_err(|e| SetOpError::compute("input_persist", e.to_string()))?;
        self.event(
            "input_saved",
            Some(serde_json::json!({ "file": name, "bytes": bytes.len() })),
            Some("raw request payload retained for replay"),
        )?;
        Ok(path)
    }
}

fn infer_state(log_path: &Path) -> RunState {
    let Ok(text) = fs::read_to_string(log_path) else {
        return RunState::Created;
    };
    if text.lines().any(|l| l.contains("\"run_succeeded\"")) {
        RunState::Succeeded
    } else if text.lines().any(|l| l.contains("\"run_failed\"")) {
        RunState::Failed
    } else if text.lines().any(|l| l.contains("\"run_started\"")) {
        RunState::Running
    } else {
        RunState::Created
    }
}

fn now_ms() -> u128 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0)
}

fn error_kind_hint(code: &str) -> &'static str {
    match code {
        "schema_mismatch" | "type_mismatch" | "row_arity" | "empty_schema" | "invalid_budget"
        | "row_not_array" | "duplicate_field" | "missing_field" | "unknown_field"
        | "empty_field_name" | "empty_struct" | "bad_operator" | "bad_quantifier"
        | "missing_input" => "input",
        "run_exists" | "illegal_transition" => "state_conflict",
        "count_overflow"
        | "spill_byte_limit"
        | "spill_file_limit"
        | "partition_too_large"
        | "partition_key_too_large" => "resource_exhausted",
        _ => "computation_failed",
    }
}

/// Process-wide run-log root, set once at startup.
static RUN_ROOT: OnceLock<PathBuf> = OnceLock::new();

pub fn init_run_root(path: PathBuf) -> Result<()> {
    fs::create_dir_all(&path).map_err(|e| SetOpError::compute("run_root_create", e.to_string()))?;
    RUN_ROOT
        .set(path)
        .map_err(|_| SetOpError::compute("run_root_twice", "run root initialized twice"))
}

pub fn run_root() -> PathBuf {
    RUN_ROOT
        .get()
        .cloned()
        .unwrap_or_else(|| PathBuf::from("./.setops-data/runs"))
}

/// Open (or create) the run log file for direct writes in tests.
pub fn open_log(path: &Path) -> Result<File> {
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|e| SetOpError::compute("log_open", e.to_string()))
}
