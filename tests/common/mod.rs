#![allow(dead_code)]

//! Shared test fixtures: an independent full-copy reference model and a
//! structured run logger.
//!
//! The reference model is deliberately naive (every fork copies every
//! page) and shares no code with the engine under test, so expected
//! snapshot contents are derived independently.

use std::{
    collections::HashMap,
    fs,
    path::PathBuf,
};

use cow_snap::{config::Config, engine::CowEngine};
use serde_json::{Value, json};

/// Small geometry so page contents are easy to reason about.
pub const TEST_PAGE_SIZE: usize = 16;
pub const TEST_LOGICAL_PAGES: usize = 8;

pub fn test_config(dir: &std::path::Path, capacity_pages: usize) -> Config {
    Config {
        page_size: TEST_PAGE_SIZE,
        logical_pages: TEST_LOGICAL_PAGES,
        capacity_pages,
        data_dir: dir.to_path_buf(),
    }
}

pub fn open_engine(dir: &std::path::Path, capacity_pages: usize) -> CowEngine {
    CowEngine::open(test_config(dir, capacity_pages)).expect("engine opens")
}

/// Fill pattern: page-sized buffer tagged with a human-readable label.
pub fn pattern(tag: u8) -> Vec<u8> {
    vec![tag; TEST_PAGE_SIZE]
}

/// Independent full-copy reference: fork clones the whole address space.
pub struct RefModel {
    page_size: usize,
    logical_pages: usize,
    snaps: HashMap<u64, Vec<Vec<u8>>>,
    next: u64,
}

impl RefModel {
    pub fn new() -> Self {
        RefModel {
            page_size: TEST_PAGE_SIZE,
            logical_pages: TEST_LOGICAL_PAGES,
            snaps: HashMap::new(),
            next: 1,
        }
    }

    pub fn create(&mut self, parent: Option<u64>) -> u64 {
        let space = match parent {
            None => vec![vec![0u8; self.page_size]; self.logical_pages],
            Some(p) => self.snaps[&p].clone(),
        };
        let id = self.next;
        self.next += 1;
        self.snaps.insert(id, space);
        id
    }

    pub fn write(&mut self, id: u64, page: usize, offset: usize, data: &[u8]) {
        let space = self.snaps.get_mut(&id).expect("ref snapshot exists");
        space[page][offset..offset + data.len()].copy_from_slice(data);
    }

    pub fn read(&self, id: u64, page: usize) -> &[u8] {
        &self.snaps[&id][page]
    }

    pub fn delete(&mut self, id: u64) {
        self.snaps.remove(&id).expect("ref snapshot exists");
    }
}

/// Assert every logical page of `snap` in the engine equals the
/// reference model.
pub fn assert_snapshot_matches(engine: &CowEngine, model: &RefModel, snap: u64) {
    for page in 0..TEST_LOGICAL_PAGES {
        let got = engine.read_page(snap, page).expect("page reads");
        let want = model.read(snap, page);
        assert_eq!(
            got,
            want,
            "snapshot {snap} page {page} diverged from full-copy reference"
        );
    }
}

/// Structured test run log: one JSON object per line, carrying the run
/// id, key intermediate states, and the rationale for each assertion,
/// so a failing scenario can be replayed from the log alone.
pub struct RunLog {
    run_id: String,
    test: String,
    seq: u64,
    events: Vec<Value>,
}

impl RunLog {
    pub fn new(test: &str) -> Self {
        RunLog {
            run_id: uuid::Uuid::new_v4().to_string(),
            test: test.to_string(),
            seq: 0,
            events: Vec::new(),
        }
    }

    pub fn event(&mut self, step: &str, state: Value, rationale: &str) {
        self.seq += 1;
        self.events.push(json!({
            "run_id": self.run_id,
            "test": self.test,
            "seq": self.seq,
            "step": step,
            "state": state,
            "rationale": rationale,
        }));
    }

    /// Persist to `test-logs/<test>-<run_id>.jsonl` and return the path.
    pub fn finish(self) -> PathBuf {
        let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("test-logs");
        fs::create_dir_all(&dir).expect("create test-logs dir");
        let path = dir.join(format!("{}-{}.jsonl", self.test, self.run_id));
        let mut out = String::new();
        for e in &self.events {
            out.push_str(&serde_json::to_string(e).unwrap());
            out.push('\n');
        }
        fs::write(&path, out).expect("write run log");
        eprintln!("run log: {}", path.display());
        path
    }
}
