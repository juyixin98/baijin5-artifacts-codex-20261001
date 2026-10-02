//! Persistent state: merge run reports stored as JSON files, one per run.
//!
//! Layout: `<state_dir>/runs/<run_id>.json`. Run ids are validated against
//! a strict charset before ever touching the filesystem, so a hostile id
//! cannot traverse out of the runs directory.

use crate::model::MergeReport;
use serde::Serialize;
use std::fmt;
use std::io;
use std::path::{Path, PathBuf};

#[derive(Debug)]
pub enum StoreError {
    InvalidRunId(String),
    Io(io::Error),
    Corrupt { run_id: String, source: serde_json::Error },
}

impl fmt::Display for StoreError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            StoreError::InvalidRunId(id) => write!(f, "invalid run id: {id:?}"),
            StoreError::Io(e) => write!(f, "store I/O error: {e}"),
            StoreError::Corrupt { run_id, source } => {
                write!(f, "stored report {run_id} is corrupt: {source}")
            }
        }
    }
}

impl std::error::Error for StoreError {}

/// Run ids: alphanumeric start, then alphanumeric / `.` / `_` / `-`,
/// max 128 chars. No separators, no dots-only names — traversal-safe.
pub fn run_id_valid(id: &str) -> bool {
    let mut chars = id.chars();
    match chars.next() {
        Some(c) if c.is_ascii_alphanumeric() => {}
        _ => return false,
    }
    id.len() <= 128
        && chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
}

/// Summary of a stored run, for the list endpoint.
#[derive(Debug, Clone, Serialize)]
pub struct RunSummary {
    pub run_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
    pub finished_at: String,
    pub entries: usize,
    pub failures: usize,
    pub uncertainties: usize,
}

pub struct RunStore {
    dir: PathBuf,
}

impl RunStore {
    pub fn new(state_dir: &Path) -> Self {
        Self {
            dir: state_dir.join("runs"),
        }
    }

    fn path_for(&self, run_id: &str) -> Result<PathBuf, StoreError> {
        if !run_id_valid(run_id) {
            return Err(StoreError::InvalidRunId(run_id.to_string()));
        }
        Ok(self.dir.join(format!("{run_id}.json")))
    }

    /// Persist a report atomically (write temp, then rename).
    pub fn save(&self, report: &MergeReport) -> Result<(), StoreError> {
        std::fs::create_dir_all(&self.dir).map_err(StoreError::Io)?;
        let path = self.path_for(&report.run_id)?;
        let tmp = self.dir.join(format!(".{}.tmp", report.run_id));
        let bytes = serde_json::to_vec_pretty(report)
            .expect("MergeReport serialization is total");
        std::fs::write(&tmp, bytes).map_err(StoreError::Io)?;
        std::fs::rename(&tmp, &path).map_err(StoreError::Io)?;
        Ok(())
    }

    pub fn load(&self, run_id: &str) -> Result<Option<MergeReport>, StoreError> {
        let path = self.path_for(run_id)?;
        let bytes = match std::fs::read(&path) {
            Ok(b) => b,
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(e) => return Err(StoreError::Io(e)),
        };
        let report = serde_json::from_slice(&bytes).map_err(|e| StoreError::Corrupt {
            run_id: run_id.to_string(),
            source: e,
        })?;
        Ok(Some(report))
    }

    pub fn list(&self) -> Result<Vec<RunSummary>, StoreError> {
        let mut out = Vec::new();
        let entries = match std::fs::read_dir(&self.dir) {
            Ok(e) => e,
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(out),
            Err(e) => return Err(StoreError::Io(e)),
        };
        for entry in entries {
            let entry = entry.map_err(StoreError::Io)?;
            let name = entry.file_name().to_string_lossy().into_owned();
            let Some(run_id) = name.strip_suffix(".json") else {
                continue;
            };
            if !run_id_valid(run_id) {
                continue;
            }
            if let Ok(Some(report)) = self.load(run_id) {
                out.push(RunSummary {
                    run_id: report.run_id,
                    request_id: report.request_id,
                    finished_at: report.finished_at,
                    entries: report.entries.len(),
                    failures: report.failures.len(),
                    uncertainties: report.uncertainties.len(),
                });
            }
        }
        out.sort_by(|a, b| a.run_id.cmp(&b.run_id));
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::*;
    use std::collections::BTreeMap;

    fn sample_report(run_id: &str) -> MergeReport {
        MergeReport {
            schema_version: REPORT_SCHEMA_VERSION,
            engine_version: ENGINE_VERSION.to_string(),
            run_id: run_id.to_string(),
            request_id: Some("req-1".to_string()),
            started_at: "2026-10-02T00:00:00Z".to_string(),
            finished_at: "2026-10-02T00:00:01Z".to_string(),
            layers: vec![],
            entries: BTreeMap::new(),
            failures: vec![],
            uncertainties: vec![],
            stats: MergeStats::default(),
        }
    }

    #[test]
    fn run_id_validation_rejects_traversal() {
        assert!(run_id_valid("abc-DEF_123.json-ish"));
        assert!(!run_id_valid("../etc/passwd"));
        assert!(!run_id_valid("a/b"));
        assert!(!run_id_valid(""));
        assert!(!run_id_valid(".hidden"));
    }

    #[test]
    fn save_load_roundtrip() {
        let tmp = tempfile::tempdir().unwrap();
        let store = RunStore::new(tmp.path());
        let report = sample_report("run-1");
        store.save(&report).unwrap();
        let loaded = store.load("run-1").unwrap().unwrap();
        assert_eq!(loaded.run_id, "run-1");
        assert_eq!(loaded.request_id.as_deref(), Some("req-1"));
        assert!(store.load("missing").unwrap().is_none());
        assert!(matches!(
            store.load("../escape"),
            Err(StoreError::InvalidRunId(_))
        ));
    }

    #[test]
    fn list_summarizes_stored_runs() {
        let tmp = tempfile::tempdir().unwrap();
        let store = RunStore::new(tmp.path());
        store.save(&sample_report("run-a")).unwrap();
        store.save(&sample_report("run-b")).unwrap();
        let runs = store.list().unwrap();
        assert_eq!(runs.len(), 2);
        assert_eq!(runs[0].run_id, "run-a");
    }
}
