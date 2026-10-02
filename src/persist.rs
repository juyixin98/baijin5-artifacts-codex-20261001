//! Filesystem persistence for runs: each run gets a directory with
//! meta.json (identity/config), report.json (metrics + checks) and
//! samples.jsonl (one sampled state per line).

use std::fs;
use std::io::Write as _;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::metrics::{RunReport, RunStatus};
use crate::scheduler::Sample;
use crate::VERSION;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunMeta {
    pub run_id: String,
    pub scenario: String,
    pub version: String,
    pub status: RunStatus,
    pub elapsed_ms: u64,
}

#[derive(Debug, Clone, Serialize)]
pub struct RunSummary {
    pub run_id: String,
    pub scenario: String,
    pub status: RunStatus,
    pub passed: bool,
}

#[derive(Debug, Error)]
pub enum StoreError {
    #[error("run '{run_id}' not found")]
    NotFound { run_id: String },
    #[error("invalid run id '{0}'")]
    InvalidRunId(String),
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("corrupt data at {path}: {reason}")]
    Corrupt { path: String, reason: String },
}

#[derive(Clone)]
pub struct RunStore {
    root: PathBuf,
}

impl RunStore {
    pub fn new(root: impl Into<PathBuf>) -> Result<Self, StoreError> {
        let store = Self { root: root.into() };
        fs::create_dir_all(store.runs_dir())?;
        Ok(store)
    }

    fn runs_dir(&self) -> PathBuf {
        self.root.join("runs")
    }

    fn valid_run_id(run_id: &str) -> bool {
        !run_id.is_empty()
            && run_id.len() <= 128
            && run_id
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    }

    fn run_dir(&self, run_id: &str) -> Result<PathBuf, StoreError> {
        if !Self::valid_run_id(run_id) {
            return Err(StoreError::InvalidRunId(run_id.to_string()));
        }
        Ok(self.runs_dir().join(run_id))
    }

    pub fn save(
        &self,
        run_id: &str,
        scenario: &str,
        report: &RunReport,
        samples: &[Sample],
    ) -> Result<(), StoreError> {
        let dir = self.run_dir(run_id)?;
        fs::create_dir_all(&dir)?;
        let meta = RunMeta {
            run_id: run_id.to_string(),
            scenario: scenario.to_string(),
            version: VERSION.to_string(),
            status: report.status,
            elapsed_ms: report.elapsed_ms,
        };
        let meta_json = serde_json::to_string_pretty(&meta)?;
        let report_json = serde_json::to_string_pretty(report)?;
        fs::write(dir.join("meta.json"), meta_json)?;
        fs::write(dir.join("report.json"), report_json)?;
        let mut buf = Vec::new();
        for sample in samples {
            serde_json::to_writer(&mut buf, sample)?;
            buf.push(b'\n');
        }
        fs::File::create(dir.join("samples.jsonl"))?.write_all(&buf)?;
        Ok(())
    }

    pub fn load_report(&self, run_id: &str) -> Result<RunReport, StoreError> {
        let path = self.run_dir(run_id)?.join("report.json");
        let text = fs::read_to_string(&path).map_err(|e| match e.kind() {
            std::io::ErrorKind::NotFound => StoreError::NotFound {
                run_id: run_id.to_string(),
            },
            _ => StoreError::Io(e),
        })?;
        serde_json::from_str(&text).map_err(|e| StoreError::Corrupt {
            path: path.display().to_string(),
            reason: e.to_string(),
        })
    }

    pub fn load_samples(&self, run_id: &str) -> Result<Vec<Sample>, StoreError> {
        let path = self.run_dir(run_id)?.join("samples.jsonl");
        let text = fs::read_to_string(&path).map_err(|e| match e.kind() {
            std::io::ErrorKind::NotFound => StoreError::NotFound {
                run_id: run_id.to_string(),
            },
            _ => StoreError::Io(e),
        })?;
        text.lines()
            .filter(|l| !l.trim().is_empty())
            .map(|line| {
                serde_json::from_str(line).map_err(|e| StoreError::Corrupt {
                    path: path.display().to_string(),
                    reason: e.to_string(),
                })
            })
            .collect()
    }

    pub fn list(&self) -> Result<Vec<RunSummary>, StoreError> {
        let mut out = Vec::new();
        let entries = match fs::read_dir(self.runs_dir()) {
            Ok(e) => e,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(out),
            Err(e) => return Err(StoreError::Io(e)),
        };
        for entry in entries {
            let entry = entry?;
            let meta_path = entry.path().join("meta.json");
            let Ok(text) = fs::read_to_string(&meta_path) else {
                continue; // not a run directory; skip
            };
            let Ok(meta) = serde_json::from_str::<RunMeta>(&text) else {
                continue; // corrupt entry: skip rather than fail the listing
            };
            let passed = self
                .load_report(&meta.run_id)
                .map(|r| r.passed)
                .unwrap_or(false);
            out.push(RunSummary {
                run_id: meta.run_id,
                scenario: meta.scenario,
                status: meta.status,
                passed,
            });
        }
        out.sort_by(|a, b| a.run_id.cmp(&b.run_id));
        Ok(out)
    }
}

// serde_json::Error -> StoreError conversion for to_string_pretty/to_writer.
impl From<serde_json::Error> for StoreError {
    fn from(e: serde_json::Error) -> Self {
        StoreError::Corrupt {
            path: "<serialize>".to_string(),
            reason: e.to_string(),
        }
    }
}
