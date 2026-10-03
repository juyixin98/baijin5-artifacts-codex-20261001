//! Persistent snapshots and sampled state history.
//!
//! Snapshot: metadata only (list membership/order, p, capacity, stats).
//! Page *content* is never serialized — clean pages are reloaded from the
//! backing store on restore, and dirty pages must be flushed through the
//! write-back adapter before a snapshot is taken (the engine enforces this).
//!
//! Sampler: every `interval` requests a compact [`StatsSample`] is appended
//! to a bounded ring, exposed via the API for trend inspection.

use std::collections::VecDeque;
use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::arc::{ArcCache, ListSnapshot, PageId, Stats};
use crate::error::ArcError;

pub const SNAPSHOT_VERSION: u32 = 1;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Snapshot {
    pub version: u32,
    pub capacity: usize,
    pub p: usize,
    pub t1: Vec<PageId>,
    pub t2: Vec<PageId>,
    pub b1: Vec<PageId>,
    pub b2: Vec<PageId>,
    pub stats: Stats,
}

impl Snapshot {
    pub fn capture(cache: &ArcCache) -> Result<Self, ArcError> {
        let dirty = cache.dirty_pages();
        if !dirty.is_empty() {
            return Err(ArcError::Snapshot {
                message: format!(
                    "refusing to snapshot with {} dirty pages (flush first): {dirty:?}",
                    dirty.len()
                ),
            });
        }
        let lists: ListSnapshot = cache.lists();
        Ok(Snapshot {
            version: SNAPSHOT_VERSION,
            capacity: cache.capacity(),
            p: cache.p(),
            t1: lists.t1,
            t2: lists.t2,
            b1: lists.b1,
            b2: lists.b2,
            stats: cache.stats().clone(),
        })
    }

    pub fn save(&self, path: &Path) -> Result<(), ArcError> {
        let json = serde_json::to_string_pretty(self)
            .map_err(|e| ArcError::Snapshot {
                message: format!("serialize snapshot: {e}"),
            })?;
        let tmp = path.with_extension("tmp");
        std::fs::write(&tmp, json).map_err(|e| ArcError::Snapshot {
            message: format!("write {}: {e}", tmp.display()),
        })?;
        std::fs::rename(&tmp, path).map_err(|e| ArcError::Snapshot {
            message: format!("rename to {}: {e}", path.display()),
        })
    }

    pub fn load(path: &Path) -> Result<Self, ArcError> {
        let text = std::fs::read_to_string(path).map_err(|e| ArcError::Snapshot {
            message: format!("read {}: {e}", path.display()),
        })?;
        let snap: Snapshot = serde_json::from_str(&text).map_err(|e| ArcError::Snapshot {
            message: format!("parse {}: {e}", path.display()),
        })?;
        if snap.version != SNAPSHOT_VERSION {
            return Err(ArcError::Snapshot {
                message: format!(
                    "unsupported snapshot version {} (expected {SNAPSHOT_VERSION})",
                    snap.version
                ),
            });
        }
        Ok(snap)
    }
}

/// One sampled observation of the adaptive state.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct StatsSample {
    pub seq: u64,
    pub p: usize,
    pub capacity: usize,
    pub t1: usize,
    pub t2: usize,
    pub b1: usize,
    pub b2: usize,
    pub hits: u64,
    pub ghost_hits: u64,
    pub misses: u64,
    pub writebacks: u64,
    pub writeback_failures: u64,
}

impl StatsSample {
    pub fn of(seq: u64, cache: &ArcCache) -> Self {
        let s = cache.stats();
        StatsSample {
            seq,
            p: cache.p(),
            capacity: cache.capacity(),
            t1: cache.lists().t1.len(),
            t2: cache.lists().t2.len(),
            b1: cache.lists().b1.len(),
            b2: cache.lists().b2.len(),
            hits: s.hits_t1 + s.hits_t2,
            ghost_hits: s.ghost_hits_b1 + s.ghost_hits_b2,
            misses: s.misses,
            writebacks: s.writebacks,
            writeback_failures: s.writeback_failures,
        }
    }
}

/// Bounded ring of samples taken every `interval` requests.
pub struct Sampler {
    interval: u64,
    capacity: usize,
    samples: VecDeque<StatsSample>,
}

impl Sampler {
    pub fn new(interval: u64, capacity: usize) -> Self {
        Sampler {
            interval: interval.max(1),
            capacity: capacity.max(1),
            samples: VecDeque::new(),
        }
    }

    pub fn maybe_sample(&mut self, seq: u64, cache: &ArcCache) {
        if !seq.is_multiple_of(self.interval) {
            return;
        }
        if self.samples.len() == self.capacity {
            self.samples.pop_front();
        }
        self.samples.push_back(StatsSample::of(seq, cache));
    }

    pub fn samples(&self) -> Vec<&StatsSample> {
        self.samples.iter().collect()
    }
}
