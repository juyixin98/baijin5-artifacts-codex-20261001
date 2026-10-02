//! Snapshot semantics: fork, atomic write batches, delete, COW planning.
//!
//! Run model: a single `CowEngine` owns all mutable state; the API layer
//! serializes access with a mutex, so every engine method executes
//! without interleaving. There is no cross-request concurrency inside
//! the engine by construction.
//!
//! Write batch contract (fixed):
//! - A batch is validated and planned as a whole. Input, state-conflict
//!   (including refcount anomalies) and capacity failures are detected
//!   BEFORE any mutation: the batch is rejected atomically.
//! - Writes to the same page within one batch are merged in order.
//! - Commit order: (1) allocate and write all new pages, (2) overwrite
//!   exclusively-owned pages in place, (3) switch the snapshot's page
//!   table and release replaced pages, (4) checkpoint the manifest.
//! - The only failure that can observe a partial batch is a compute
//!   (IO) failure during step (2); it can only affect pages exclusively
//!   owned by the target snapshot, never other snapshots.

use std::collections::{BTreeMap, HashMap};

use serde::{Deserialize, Serialize};
use tracing::instrument;

use crate::{
    config::Config,
    diag::{RefcountAnomaly, SnapshotStat, StatsReport, VerifyReport},
    error::AppError,
    persist::{self, Manifest, MANIFEST_VERSION},
    store::{Counters, PageStore, PhysId},
};

pub type SnapId = u64;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SnapshotMeta {
    /// Lineage only: informational, not used for reads (the table is
    /// fully materialized at fork time).
    pub parent: Option<SnapId>,
    /// Logical page index -> physical page. `None` reads as zeros.
    pub table: Vec<Option<PhysId>>,
}

/// One write within a batch: splice `data` into `page` at `offset`.
#[derive(Debug, Clone)]
pub struct WriteReq {
    pub page: usize,
    pub offset: usize,
    pub data: Vec<u8>,
}

/// Per-batch deltas, returned to callers and asserted on in tests.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct WriteReport {
    pub pages_written: usize,
    pub cow_copies: u64,
    pub in_place_writes: u64,
    pub fresh_allocs: u64,
}

/// How a logical page's write will be physically applied.
#[derive(Debug, Clone, Copy)]
enum Action {
    /// Never written before: allocate a zero-based page.
    Fresh,
    /// Shared page: allocate a copy, release the old reference.
    Cow(PhysId),
    /// Exclusively owned: overwrite in place.
    InPlace(PhysId),
}

pub struct CowEngine {
    config: Config,
    store: PageStore,
    snapshots: BTreeMap<SnapId, SnapshotMeta>,
    next_snap: SnapId,
}

impl CowEngine {
    /// Open the data directory, restoring the manifest if present.
    pub fn open(config: Config) -> Result<Self, AppError> {
        config.validate()?;
        std::fs::create_dir_all(&config.data_dir)?;
        match persist::load(&config.manifest_path())? {
            Some(m) => Self::restore(config, m),
            None => {
                let store = PageStore::create(
                    config.pages_dir(),
                    config.page_size,
                    config.capacity_pages,
                )?;
                let engine = CowEngine {
                    config,
                    store,
                    snapshots: BTreeMap::new(),
                    next_snap: 1,
                };
                engine.checkpoint()?;
                Ok(engine)
            }
        }
    }

    fn restore(config: Config, m: Manifest) -> Result<Self, AppError> {
        if m.page_size != config.page_size
            || m.logical_pages != config.logical_pages
            || m.capacity_pages != config.capacity_pages
        {
            return Err(AppError::Compute(format!(
                "config mismatch with manifest: manifest has page_size={}, logical_pages={}, capacity_pages={}",
                m.page_size, m.logical_pages, m.capacity_pages
            )));
        }
        let store = PageStore::restore(
            config.pages_dir(),
            config.page_size,
            config.capacity_pages,
            m.refcounts,
            m.next_phys,
            m.counters,
        );
        Ok(CowEngine {
            config,
            store,
            snapshots: m.snapshots,
            next_snap: m.next_snap,
        })
    }

    fn checkpoint(&self) -> Result<(), AppError> {
        let manifest = Manifest {
            version: MANIFEST_VERSION,
            page_size: self.config.page_size,
            logical_pages: self.config.logical_pages,
            capacity_pages: self.config.capacity_pages,
            next_snap: self.next_snap,
            next_phys: self.store.next_id(),
            refcounts: self.store.refcounts().clone(),
            snapshots: self.snapshots.clone(),
            counters: self.store.counters(),
        };
        persist::save(&self.config.manifest_path(), &manifest)
    }

    /// Verify that every physical page referenced by `table` is recorded
    /// with refcount >= 1. Refcount anomalies reject the operation
    /// before any mutation happens.
    fn guard_table(&self, snap: SnapId, table: &[Option<PhysId>]) -> Result<(), AppError> {
        for p in table.iter().flatten() {
            match self.store.refcount(*p) {
                Some(rc) if rc >= 1 => {}
                other => {
                    return Err(AppError::State(format!(
                        "refcount anomaly: snapshot {snap} references page {p} with recorded refcount {other:?}; refusing to continue"
                    )))
                }
            }
        }
        Ok(())
    }

    /// Create a snapshot: an empty one, or a fork sharing the parent's
    /// page references (refcounts incremented, nothing copied).
    #[instrument(skip(self))]
    pub fn create_snapshot(&mut self, parent: Option<SnapId>) -> Result<SnapId, AppError> {
        let table = match parent {
            None => vec![None; self.config.logical_pages],
            Some(p) => {
                let meta = self
                    .snapshots
                    .get(&p)
                    .ok_or_else(|| AppError::State(format!("unknown parent snapshot {p}")))?;
                self.guard_table(p, &meta.table)?;
                let table = meta.table.clone();
                for phys in table.iter().flatten() {
                    self.store.add_ref(*phys)?;
                }
                table
            }
        };
        let id = self.next_snap;
        self.next_snap += 1;
        self.snapshots.insert(id, SnapshotMeta { parent, table });
        self.checkpoint()?;
        Ok(id)
    }

    /// Apply a batch of writes atomically (see module docs for the
    /// contract). Returns per-batch deltas.
    #[instrument(skip(self, writes))]
    pub fn write_batch(
        &mut self,
        snap: SnapId,
        writes: &[WriteReq],
    ) -> Result<WriteReport, AppError> {
        // --- state check ---
        let meta = self
            .snapshots
            .get(&snap)
            .cloned()
            .ok_or_else(|| AppError::State(format!("unknown snapshot {snap}")))?;

        // --- input validation (whole batch, before any mutation) ---
        if writes.is_empty() {
            return Err(AppError::Input("empty write batch".into()));
        }
        for w in writes {
            if w.page >= self.config.logical_pages {
                return Err(AppError::Input(format!(
                    "page index {} out of range (logical_pages={})",
                    w.page, self.config.logical_pages
                )));
            }
            if w.data.is_empty() {
                return Err(AppError::Input(format!("empty write on page {}", w.page)));
            }
            let end = w.offset.checked_add(w.data.len()).ok_or_else(|| {
                AppError::Input(format!("offset overflow on page {}", w.page))
            })?;
            if end > self.config.page_size {
                return Err(AppError::Input(format!(
                    "write [{}..{}) exceeds page size {} on page {}",
                    w.offset, end, self.config.page_size, w.page
                )));
            }
        }

        // --- refcount anomaly guard (before any mutation) ---
        self.guard_table(snap, &meta.table)?;

        // --- merge writes per page, preserving order ---
        let mut by_page: BTreeMap<usize, Vec<&WriteReq>> = BTreeMap::new();
        for w in writes {
            by_page.entry(w.page).or_default().push(w);
        }

        // --- plan: decide Fresh/Cow/InPlace per page, count allocations ---
        let mut plan: Vec<(usize, Action)> = Vec::with_capacity(by_page.len());
        let mut allocs_needed = 0usize;
        for (page, _) in &by_page {
            let action = match meta.table[*page] {
                None => {
                    allocs_needed += 1;
                    Action::Fresh
                }
                Some(p) => {
                    // refcount was guarded >= 1 above
                    let rc = self.store.refcount(p).expect("guarded refcount");
                    if rc > 1 {
                        allocs_needed += 1;
                        Action::Cow(p)
                    } else {
                        Action::InPlace(p)
                    }
                }
            };
            plan.push((*page, action));
        }

        // --- capacity check (whole batch, before any mutation) ---
        if self.store.used() + allocs_needed > self.store.capacity() {
            self.store.counters_mut().batches_rejected += 1;
            if let Err(e) = self.checkpoint() {
                tracing::warn!(error = %e, "checkpoint after batch rejection failed");
            }
            return Err(AppError::Resource(format!(
                "batch needs {allocs_needed} new pages but only {} of {} free",
                self.store.capacity() - self.store.used(),
                self.store.capacity()
            )));
        }

        // --- commit phase 1: allocate and write all new pages ---
        let mut staged: HashMap<usize, PhysId> = HashMap::new();
        let mut report = WriteReport::default();
        for (page, action) in &plan {
            let (Action::Fresh | Action::Cow(_)) = action else {
                continue;
            };
            let content = self.build_content(&meta, &by_page, *page, *action)?;
            match self.store.alloc_with(&content) {
                Ok(id) => {
                    staged.insert(*page, id);
                    match action {
                        Action::Fresh => report.fresh_allocs += 1,
                        Action::Cow(_) => report.cow_copies += 1,
                        Action::InPlace(_) => unreachable!(),
                    }
                }
                Err(e) => {
                    // Roll back pages allocated by this batch; the old
                    // table is untouched, so the batch is fully atomic.
                    for id in staged.values() {
                        if let Err(re) = self.store.release(*id) {
                            tracing::warn!(error = %re, "rollback release failed");
                        }
                    }
                    return Err(e);
                }
            }
        }

        // --- commit phase 2: in-place writes to exclusive pages ---
        // A compute failure here may leave this snapshot's exclusive
        // pages partially updated; no other snapshot can be affected.
        for (page, action) in &plan {
            let Action::InPlace(p) = action else {
                continue;
            };
            let content = self.build_content(&meta, &by_page, *page, *action)?;
            self.store.write_in_place(*p, &content)?;
            report.in_place_writes += 1;
        }

        // --- commit phase 3: switch table, release replaced pages ---
        let mut new_table = meta.table.clone();
        for (page, action) in &plan {
            match action {
                Action::Fresh => {
                    new_table[*page] = Some(staged[page]);
                }
                Action::Cow(old) => {
                    new_table[*page] = Some(staged[page]);
                    self.store.release(*old)?;
                }
                Action::InPlace(_) => {}
            }
        }
        self.snapshots
            .get_mut(&snap)
            .expect("snapshot checked above")
            .table = new_table;

        report.pages_written = by_page.len();
        {
            let c = self.store.counters_mut();
            c.cow_copies += report.cow_copies;
            c.in_place_writes += report.in_place_writes;
            c.fresh_allocs += report.fresh_allocs;
            c.batches_applied += 1;
        }
        self.checkpoint()?;
        Ok(report)
    }

    /// Build the full new content of a logical page: base content
    /// (zeros or the current physical page) with the batch's writes
    /// spliced in order.
    fn build_content(
        &self,
        meta: &SnapshotMeta,
        by_page: &BTreeMap<usize, Vec<&WriteReq>>,
        page: usize,
        action: Action,
    ) -> Result<Vec<u8>, AppError> {
        let mut content = match action {
            Action::Fresh => vec![0u8; self.config.page_size],
            Action::Cow(p) | Action::InPlace(p) => self.store.read(p)?,
        };
        debug_assert_eq!(meta.table[page].is_some(), !matches!(action, Action::Fresh));
        for w in &by_page[&page] {
            content[w.offset..w.offset + w.data.len()].copy_from_slice(&w.data);
        }
        Ok(content)
    }

    /// Delete a snapshot. Reclaimed pages are only those no other
    /// snapshot references; shared pages survive via their refcounts.
    #[instrument(skip(self))]
    pub fn delete_snapshot(&mut self, snap: SnapId) -> Result<(), AppError> {
        let meta = self
            .snapshots
            .get(&snap)
            .cloned()
            .ok_or_else(|| AppError::State(format!("unknown snapshot {snap}")))?;
        self.guard_table(snap, &meta.table)?;
        self.snapshots.remove(&snap);
        for phys in meta.table.iter().flatten() {
            self.store.release(*phys)?;
        }
        self.checkpoint()?;
        Ok(())
    }

    /// Read a full logical page (zeros if never written).
    pub fn read_page(&self, snap: SnapId, page: usize) -> Result<Vec<u8>, AppError> {
        if page >= self.config.logical_pages {
            return Err(AppError::Input(format!(
                "page index {page} out of range (logical_pages={})",
                self.config.logical_pages
            )));
        }
        let meta = self
            .snapshots
            .get(&snap)
            .ok_or_else(|| AppError::State(format!("unknown snapshot {snap}")))?;
        match meta.table[page] {
            None => Ok(vec![0u8; self.config.page_size]),
            Some(p) => self.store.read(p),
        }
    }

    pub fn stats(&self) -> StatsReport {
        let snapshots = self
            .snapshots
            .iter()
            .map(|(id, meta)| {
                let live = meta.table.iter().flatten().count();
                let shared = meta
                    .table
                    .iter()
                    .flatten()
                    .filter(|p| self.store.refcount(**p).unwrap_or(0) > 1)
                    .count();
                SnapshotStat { id: *id, parent: meta.parent, live_pages: live, shared_pages: shared }
            })
            .collect();
        StatsReport {
            page_size: self.config.page_size,
            logical_pages: self.config.logical_pages,
            capacity_pages: self.store.capacity(),
            used_pages: self.store.used(),
            free_pages: self.store.capacity() - self.store.used(),
            snapshot_count: self.snapshots.len(),
            snapshots,
            counters: self.store.counters(),
        }
    }

    /// Recompute refcounts from all snapshot tables and compare with the
    /// store's records. Read-only; never mutates.
    pub fn verify(&self) -> VerifyReport {
        let mut recomputed: BTreeMap<PhysId, u32> = BTreeMap::new();
        for meta in self.snapshots.values() {
            for p in meta.table.iter().flatten() {
                *recomputed.entry(*p).or_insert(0) += 1;
            }
        }
        let mut anomalies = Vec::new();
        for (p, recorded) in self.store.refcounts() {
            let got = recomputed.get(p).copied().unwrap_or(0);
            if got != *recorded {
                anomalies.push(RefcountAnomaly {
                    page: *p,
                    recorded: Some(*recorded),
                    recomputed: got,
                });
            }
        }
        for (p, got) in &recomputed {
            if !self.store.refcounts().contains_key(p) {
                anomalies.push(RefcountAnomaly { page: *p, recorded: None, recomputed: *got });
            }
        }
        VerifyReport { ok: anomalies.is_empty(), anomalies }
    }

    // --- test/diagnostic accessors ---

    pub fn counters(&self) -> Counters {
        self.store.counters()
    }

    pub fn used_pages(&self) -> usize {
        self.store.used()
    }

    pub fn snapshot_exists(&self, snap: SnapId) -> bool {
        self.snapshots.contains_key(&snap)
    }

    /// Diagnostic hook: force a refcount value to exercise anomaly
    /// handling. Never used by the engine itself.
    #[doc(hidden)]
    pub fn debug_force_refcount(&mut self, page: PhysId, rc: u32) {
        self.store.debug_force_refcount(page, rc);
    }
}
