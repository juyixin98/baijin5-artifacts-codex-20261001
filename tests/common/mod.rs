//! Shared test infrastructure.
//!
//! `FullCopyRef` is the *independent reference implementation*: it models
//! snapshots as full deep copies with plain `std` collections and shares no
//! code with the engine under test. Every contract test drives both
//! implementations through the same operation sequence and compares
//! page-for-page. Expected copy counts are hardcoded in each test from
//! first-principles reasoning, never read back from the engine.
//!
//! Each integration-test binary compiles this module and uses a different
//! subset of it, hence the crate-level allow.
#![allow(dead_code)]

use std::collections::BTreeMap;
use std::path::PathBuf;

use cow_snapshot_service::config::ServiceConfig;
use cow_snapshot_service::engine::Engine;

/// Independent full-copy reference: a snapshot is a deep copy of the whole
/// volume. No sharing, no refcounts — trivially correct by inspection.
pub struct FullCopyRef {
    pub page_size: usize,
    pub live: Vec<Vec<u8>>,
    pub snaps: BTreeMap<u64, (String, Vec<Vec<u8>>)>,
}

impl FullCopyRef {
    pub fn new(page_size: usize, page_count: usize) -> Self {
        Self {
            page_size,
            live: vec![vec![0u8; page_size]; page_count],
            snaps: BTreeMap::new(),
        }
    }

    /// Apply writes sequentially; later writes in the batch override earlier
    /// ones on the same page (same contract as the engine).
    pub fn batch_write(&mut self, writes: &[(u32, u32, Vec<u8>)]) {
        for (page, offset, data) in writes {
            let start = *offset as usize;
            self.live[*page as usize][start..start + data.len()].copy_from_slice(data);
        }
    }

    pub fn fork(&mut self, id: u64, name: &str) {
        self.snaps.insert(id, (name.to_string(), self.live.clone()));
    }

    pub fn delete(&mut self, id: u64) {
        self.snaps.remove(&id);
    }

    pub fn live_page(&self, page: usize) -> &[u8] {
        &self.live[page]
    }

    pub fn snap_page(&self, id: u64, page: usize) -> &[u8] {
        &self.snaps.get(&id).unwrap().1[page]
    }
}

/// Assert every live page and every snapshot page of `engine` matches `ref_`.
pub fn assert_matches_reference(engine: &Engine, ref_: &FullCopyRef, ctx: &str) {
    for page in 0..ref_.live.len() {
        let got = engine.read_live_page(page as u32).unwrap();
        assert_eq!(
            got,
            ref_.live_page(page),
            "{ctx}: live page {page} diverged from full-copy reference"
        );
    }
    for (&id, (name, _)) in &ref_.snaps {
        for page in 0..ref_.live.len() {
            let got = engine.read_snapshot_page(id, page as u32).unwrap();
            assert_eq!(
                got,
                ref_.snap_page(id, page),
                "{ctx}: snapshot {id} ({name}) page {page} diverged from full-copy reference"
            );
        }
    }
}

/// Deterministic page-filling pattern, distinct per `seed`.
pub fn pattern(seed: u8, len: usize) -> Vec<u8> {
    (0..len).map(|i| seed ^ (i as u8)).collect()
}

/// Write `pattern(seed, page_size)` at offset 0 of `page`.
pub fn full_page_write(page: u32, seed: u8, page_size: usize) -> (u32, u32, Vec<u8>) {
    (page, 0, pattern(seed, page_size))
}

pub fn to_page_writes(
    writes: &[(u32, u32, Vec<u8>)],
) -> Vec<cow_snapshot_service::engine::PageWrite> {
    writes
        .iter()
        .map(
            |(page, offset, data)| cow_snapshot_service::engine::PageWrite {
                page: *page,
                offset: *offset,
                data: data.clone(),
            },
        )
        .collect()
}

/// Per-test data dir under target/, wiped on entry so tests are repeatable
/// and their event logs stay available for post-mortem replay.
pub fn test_dir(test_name: &str) -> PathBuf {
    let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("target")
        .join("cow-test-data")
        .join(test_name);
    if dir.exists() {
        std::fs::remove_dir_all(&dir).unwrap();
    }
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

pub fn test_cfg(
    dir: PathBuf,
    page_size: usize,
    page_count: usize,
    capacity: usize,
) -> ServiceConfig {
    ServiceConfig {
        data_dir: dir,
        page_size,
        page_count,
        capacity,
        bind: "127.0.0.1:0".to_string(),
        max_batch_writes: 1024,
    }
}

/// Log a line with test name + engine run id so failures can be replayed
/// against the persisted event log in the test's data dir.
#[macro_export]
macro_rules! tlog {
    ($engine:expr, $($arg:tt)*) => {
        eprintln!("[TEST {}][run={}] {}", module_path!(), $engine.run_id(), format!($($arg)*))
    };
}
