//! Run persistence: in-memory index plus an append-only JSONL journal and a
//! summary JSON per run under `<data_dir>/runs/`. The journal is the sampled,
//! replayable state of the service; the in-memory map is only a cache.

use crate::cost::RunSummary;
use crate::engine::Event;
use crate::error::ApiError;
use serde::Serialize;
use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;

#[derive(Clone, Serialize)]
pub struct RunRecord {
    pub summary: RunSummary,
    pub events: Vec<Event>,
}

struct Inner {
    runs: HashMap<String, RunRecord>,
}

pub struct RunStore {
    dir: PathBuf,
    inner: Mutex<Inner>,
    counter: AtomicU64,
}

impl RunStore {
    pub fn new(data_dir: &str) -> std::io::Result<RunStore> {
        let dir = PathBuf::from(data_dir).join("runs");
        fs::create_dir_all(&dir)?;
        Ok(RunStore {
            dir,
            inner: Mutex::new(Inner {
                runs: HashMap::new(),
            }),
            counter: AtomicU64::new(1),
        })
    }

    pub fn next_run_id(&self) -> String {
        format!("run-{:06}", self.counter.fetch_add(1, Ordering::SeqCst))
    }

    /// Persist (journal + summary) and cache a finished run.
    pub fn save(&self, record: RunRecord) -> Result<(), ApiError> {
        let id = &record.summary.run_id;
        let events_path = self.dir.join(format!("{id}.events.jsonl"));
        let mut buf = String::new();
        for e in &record.events {
            buf.push_str(&serde_json::to_string(e)?);
            buf.push('\n');
        }
        fs::write(&events_path, buf)?;
        let summary_path = self.dir.join(format!("{id}.summary.json"));
        fs::write(&summary_path, serde_json::to_string_pretty(&record.summary)?)?;
        self.inner
            .lock()
            .expect("store mutex")
            .runs
            .insert(id.clone(), record);
        Ok(())
    }

    pub fn get(&self, id: &str) -> Result<RunRecord, ApiError> {
        if let Some(r) = self.inner.lock().expect("store mutex").runs.get(id) {
            return Ok(r.clone());
        }
        // Cold path: reload from the journal directory.
        let summary_path = self.dir.join(format!("{id}.summary.json"));
        let events_path = self.dir.join(format!("{id}.events.jsonl"));
        let summary_text = fs::read_to_string(&summary_path).map_err(|_| {
            ApiError::NotFound(format!("run {id:?} not found"))
        })?;
        let summary: RunSummary = serde_json::from_str(&summary_text)?;
        let events_text = fs::read_to_string(&events_path)?;
        let mut events = Vec::new();
        for line in events_text.lines() {
            if !line.trim().is_empty() {
                events.push(serde_json::from_str(line)?);
            }
        }
        let record = RunRecord { summary, events };
        self.inner
            .lock()
            .expect("store mutex")
            .runs
            .insert(id.to_string(), record.clone());
        Ok(record)
    }

    pub fn list(&self) -> Vec<String> {
        let mut ids: Vec<String> = fs::read_dir(&self.dir)
            .map(|rd| {
                rd.filter_map(|e| e.ok())
                    .filter_map(|e| {
                        let name = e.file_name().to_string_lossy().into_owned();
                        name.strip_suffix(".summary.json").map(|s| s.to_string())
                    })
                    .collect()
            })
            .unwrap_or_default();
        ids.sort();
        ids
    }

    pub fn journal_file_count(&self) -> usize {
        self.list().len()
    }
}
