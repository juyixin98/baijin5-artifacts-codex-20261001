//! Copy-on-write snapshot engine.
//!
//! Data model:
//! - The *live volume* is a `Vec<ObjId>` of length `page_count`.
//! - A *snapshot* is a frozen copy of that vector plus a name.
//! - Page objects are shared between the live volume and snapshots purely by
//!   reference; the store's refcount table tracks how many mappings point at
//!   each object.
//!
//! Core contracts:
//!
//! 1. **First write copies only affected pages.** A batch write copies exactly
//!    the touched pages whose object is shared (refcount > 1). Exclusive
//!    objects (refcount == 1) are overwritten in place — no copy, no new
//!    object.
//! 2. **Atomic batches.** A multi-page batch is validated and capacity-checked
//!    before any mutation, applied under the engine lock, and the live mapping
//!    is only updated once every store operation succeeded. On failure the
//!    undo log restores the previous state: observers see either the full
//!    batch or none of it. (Crash-atomicity is best-effort: metadata is
//!    written tmp+rename, but there is no write-ahead journal — see README.)
//! 3. **Deletion only reclaims unreferenced objects.** Deleting a snapshot
//!    decrements refcounts; an object is freed only when its count reaches
//!    zero, so other snapshots and the live volume are never affected.
//! 4. **Refcount anomalies stop writes.** Before and after every mutation the
//!    engine checks `sum(refcounts) == page_count * (1 + #snapshots)` plus
//!    per-object invariants. Any violation quarantines the service: all
//!    further mutations are rejected with `StateConflict/quarantined` until an
//!    operator clears the quarantine (which requires a clean audit).

use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::config::ServiceConfig;
use crate::error::{ErrorCategory, Result, ServiceError};
use crate::eventlog::{Event, EventLog};
use crate::page_store::{ObjId, PageStore};

/// One page write inside an atomic batch.
#[derive(Debug, Clone)]
pub struct PageWrite {
    pub page: u32,
    pub offset: u32,
    pub data: Vec<u8>,
}

#[derive(Debug, Clone, Serialize)]
pub struct BatchReport {
    pub batch_seq: u64,
    pub pages_touched: usize,
    pub pages_copied: usize,
    pub pages_in_place: usize,
    pub objects_used: usize,
    pub capacity: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct SnapshotInfo {
    pub id: u64,
    pub name: String,
    pub created_seq: u64,
    pub pages_shared_with_live: usize,
    pub pages_exclusive: usize,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Stats {
    pub batches_committed: u64,
    pub writes_total: u64,
    pub pages_copied_total: u64,
    pub pages_in_place_total: u64,
}

#[derive(Debug, Clone, Serialize)]
pub struct RefMismatch {
    pub obj: ObjId,
    pub expected: u32,
    pub actual: u32,
}

#[derive(Debug, Clone, Serialize)]
pub struct AuditReport {
    pub ok: bool,
    pub expected_total_refs: u64,
    pub actual_total_refs: u64,
    pub mismatches: Vec<RefMismatch>,
    /// Table entries whose object file is missing on disk.
    pub missing_files: Vec<ObjId>,
    /// Object files on disk with no table entry (crash residue). Reported
    /// but not treated as corruption: they hold no references.
    pub orphan_files: Vec<ObjId>,
}

#[derive(Debug, Clone, Serialize)]
pub struct StatsView {
    pub run_id: String,
    pub page_size: usize,
    pub page_count: usize,
    pub capacity: usize,
    pub objects_used: usize,
    pub total_refs: u64,
    pub expected_total_refs: u64,
    pub snapshots: usize,
    pub quarantined: bool,
    pub quarantine_reason: Option<String>,
    pub stats: Stats,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Snapshot {
    name: String,
    created_seq: u64,
    mapping: Vec<ObjId>,
}

#[derive(Serialize, Deserialize)]
struct EngineMeta {
    page_count: usize,
    live: Vec<ObjId>,
    snapshots: BTreeMap<u64, Snapshot>,
    next_snap_id: u64,
    op_seq: u64,
    quarantine: Option<String>,
    stats: Stats,
}

/// One reversible step of a batch write.
enum UndoStep {
    Copied {
        page: usize,
        old_obj: ObjId,
        new_obj: ObjId,
    },
    Overwritten {
        obj: ObjId,
        old_content: Vec<u8>,
    },
}

enum Plan {
    Copy,
    InPlace,
}

pub struct Engine {
    store: PageStore,
    live: Vec<ObjId>,
    snapshots: BTreeMap<u64, Snapshot>,
    next_snap_id: u64,
    op_seq: u64,
    quarantine: Option<String>,
    stats: Stats,
    events: EventLog,
    page_count: usize,
    max_batch_writes: usize,
    meta_path: PathBuf,
}

impl Engine {
    /// Open the data directory, initializing a fresh volume when no engine
    /// metadata exists. Inconsistencies found at open do not prevent startup;
    /// they put the service into quarantine so operators can inspect it.
    pub fn init_or_open(cfg: &ServiceConfig) -> Result<Self> {
        let store = PageStore::open(&cfg.data_dir, cfg.page_size, cfg.capacity)?;
        let events = EventLog::new(&cfg.data_dir.join("log"), &crate::eventlog::new_run_id())?;
        let meta_path = cfg.data_dir.join("meta").join("engine.json");

        if meta_path.exists() {
            let raw = fs::read_to_string(&meta_path)
                .map_err(|e| ServiceError::internal("io", format!("read engine.json: {e}")))?;
            let meta: EngineMeta = serde_json::from_str(&raw).map_err(|e| {
                ServiceError::integrity("meta_corrupt", format!("parse engine.json: {e}"))
            })?;
            if meta.page_count != cfg.page_count {
                return Err(ServiceError::conflict(
                    "config_mismatch",
                    format!(
                        "engine page_count {} != configured {}",
                        meta.page_count, cfg.page_count
                    ),
                ));
            }
            let mut engine = Self {
                store,
                live: meta.live,
                snapshots: meta.snapshots,
                next_snap_id: meta.next_snap_id,
                op_seq: meta.op_seq,
                quarantine: meta.quarantine,
                stats: meta.stats,
                events,
                page_count: cfg.page_count,
                max_batch_writes: cfg.max_batch_writes,
                meta_path,
            };
            engine.events.record_ok(
                "engine.open",
                json!({
                    "objects_used": engine.store.len(),
                    "snapshots": engine.snapshots.len(),
                    "resumed_quarantine": engine.quarantine,
                }),
            );
            // Open-time audit: any inconsistency quarantines the service.
            let report = engine.compute_audit()?;
            if !report.ok && engine.quarantine.is_none() {
                let reason = "open-time audit failed".to_string();
                engine.events.record_err(
                    "quarantine.set",
                    &ServiceError::integrity("refcount_anomaly", reason.clone()),
                    json!({ "audit": &report }),
                );
                engine.quarantine = Some(reason);
                engine.persist_meta()?;
            }
            Ok(engine)
        } else {
            let mut engine = Self {
                store,
                live: Vec::new(),
                snapshots: BTreeMap::new(),
                next_snap_id: 1,
                op_seq: 0,
                quarantine: None,
                stats: Stats::default(),
                events,
                page_count: cfg.page_count,
                max_batch_writes: cfg.max_batch_writes,
                meta_path,
            };
            // Seed: every live page points at one shared zero object.
            let zero = vec![0u8; cfg.page_size];
            let zero_id = engine
                .store
                .create_with_refcount(&zero, cfg.page_count as u32)?;
            engine.live = vec![zero_id; cfg.page_count];
            engine.store.persist()?;
            engine.persist_meta()?;
            engine.events.record_ok(
                "engine.init",
                json!({
                    "page_size": cfg.page_size,
                    "page_count": cfg.page_count,
                    "capacity": cfg.capacity,
                    "zero_obj": zero_id,
                }),
            );
            Ok(engine)
        }
    }

    // ------------------------------------------------------------------
    // Writes
    // ------------------------------------------------------------------

    /// Apply a batch of page writes atomically. See module docs for the
    /// contract. Returns the per-batch copy/in-place breakdown.
    pub fn batch_write(&mut self, writes: Vec<PageWrite>) -> Result<BatchReport> {
        self.op_seq += 1;
        let batch_seq = self.op_seq;
        let detail_base = json!({ "batch_seq": batch_seq, "writes": writes.len() });

        let result = self.batch_write_inner(batch_seq, writes);
        self.quarantine_on_integrity(&result, &format!("batch {batch_seq}"));
        match &result {
            Ok(report) => {
                self.events.record_ok(
                    "batch_write",
                    json!({
                        "batch_seq": batch_seq,
                        "pages_touched": report.pages_touched,
                        "pages_copied": report.pages_copied,
                        "pages_in_place": report.pages_in_place,
                        "objects_used": report.objects_used,
                        "total_refs": self.store.total_refs(),
                        "expected_total_refs": self.expected_total_refs(),
                    }),
                );
            }
            Err(e) => {
                self.events.record_err("batch_write", e, detail_base);
            }
        }
        result
    }

    fn batch_write_inner(&mut self, batch_seq: u64, writes: Vec<PageWrite>) -> Result<BatchReport> {
        self.check_writable()?;

        // --- Input validation (no mutation below this line on error) ---
        if writes.is_empty() {
            return Err(ServiceError::input(
                "empty_batch",
                "batch contains no writes",
            ));
        }
        if writes.len() > self.max_batch_writes {
            return Err(ServiceError::input(
                "batch_too_large",
                format!(
                    "{} writes exceeds max_batch_writes={}",
                    writes.len(),
                    self.max_batch_writes
                ),
            ));
        }
        let page_size = self.store.page_size();
        for w in &writes {
            if w.page as usize >= self.page_count {
                return Err(ServiceError::input(
                    "page_out_of_range",
                    format!(
                        "page {} out of range (page_count={})",
                        w.page, self.page_count
                    ),
                ));
            }
            if w.data.is_empty() {
                return Err(ServiceError::input(
                    "empty_write",
                    format!("page {}: empty data", w.page),
                ));
            }
            let end = w.offset as usize + w.data.len();
            if end > page_size {
                return Err(ServiceError::input(
                    "write_out_of_bounds",
                    format!(
                        "page {}: offset {} + len {} exceeds page_size {}",
                        w.page,
                        w.offset,
                        w.data.len(),
                        page_size
                    ),
                ));
            }
        }

        // Merge writes per page, preserving submission order within a page.
        let mut by_page: BTreeMap<usize, Vec<&PageWrite>> = BTreeMap::new();
        for w in &writes {
            by_page.entry(w.page as usize).or_default().push(w);
        }

        // --- Integrity pre-check: refuse to write on anomalous state ---
        self.check_refcount_invariant()?;

        // --- Plan: shared objects are copied, exclusive ones in place ---
        let mut plan: BTreeMap<usize, Plan> = BTreeMap::new();
        for &page in by_page.keys() {
            let obj = self.live[page];
            let rc = self.store.refcount(obj).ok_or_else(|| {
                ServiceError::integrity(
                    "refcount_anomaly",
                    format!("live page {page} references unknown object {obj}"),
                )
            })?;
            plan.insert(page, if rc > 1 { Plan::Copy } else { Plan::InPlace });
        }
        let copies_needed = plan.values().filter(|p| matches!(p, Plan::Copy)).count();

        // --- Capacity pre-check: fail before any mutation ---
        let available = self.store.capacity() - self.store.len();
        if copies_needed > available {
            return Err(ServiceError::exhausted(
                "capacity_exhausted",
                format!(
                    "batch needs {copies_needed} new page objects, only {available} available \
                     (used={} capacity={}); batch rejected atomically",
                    self.store.len(),
                    self.store.capacity()
                ),
            ));
        }

        // --- Execute with an undo log ---
        let mut undo: Vec<UndoStep> = Vec::new();
        let exec = (|| -> Result<()> {
            for (&page, page_writes) in &by_page {
                let obj = self.live[page];
                match plan[&page] {
                    Plan::Copy => {
                        let mut content = self.store.read(obj)?;
                        for w in page_writes {
                            let start = w.offset as usize;
                            content[start..start + w.data.len()].copy_from_slice(&w.data);
                        }
                        let new_obj = self.store.create(&content)?;
                        // rc > 1 here, so release can never free the old
                        // object — the undo step can add_ref it back exactly.
                        self.store.release(obj)?;
                        self.live[page] = new_obj;
                        undo.push(UndoStep::Copied {
                            page,
                            old_obj: obj,
                            new_obj,
                        });
                    }
                    Plan::InPlace => {
                        let old_content = self.store.read(obj)?;
                        let mut content = old_content.clone();
                        for w in page_writes {
                            let start = w.offset as usize;
                            content[start..start + w.data.len()].copy_from_slice(&w.data);
                        }
                        self.store.overwrite(obj, &content)?;
                        undo.push(UndoStep::Overwritten { obj, old_content });
                    }
                }
            }
            Ok(())
        })();

        if let Err(e) = exec {
            self.rollback(undo);
            self.quarantine(&format!(
                "batch {batch_seq} failed mid-commit, rolled back: {e}"
            ));
            return Err(e);
        }

        // --- Integrity post-check ---
        if let Err(e) = self.check_refcount_invariant() {
            self.rollback(undo);
            self.quarantine(&format!(
                "batch {batch_seq} violated refcount invariant, rolled back"
            ));
            return Err(e);
        }

        // --- Commit: update stats, then persist store table + metadata ---
        let pages_copied = undo
            .iter()
            .filter(|s| matches!(s, UndoStep::Copied { .. }))
            .count() as u64;
        let pages_in_place = undo.len() as u64 - pages_copied;
        self.stats.batches_committed += 1;
        self.stats.writes_total += writes_total(&by_page);
        self.stats.pages_copied_total += pages_copied;
        self.stats.pages_in_place_total += pages_in_place;

        if let Err(e) = self.store.persist().and_then(|_| self.persist_meta()) {
            self.quarantine(&format!("persist failed after batch {batch_seq}: {e}"));
            return Err(e);
        }

        Ok(BatchReport {
            batch_seq,
            pages_touched: by_page.len(),
            pages_copied: pages_copied as usize,
            pages_in_place: pages_in_place as usize,
            objects_used: self.store.len(),
            capacity: self.store.capacity(),
        })
    }

    fn rollback(&mut self, undo: Vec<UndoStep>) {
        for step in undo.into_iter().rev() {
            match step {
                UndoStep::Copied {
                    page,
                    old_obj,
                    new_obj,
                } => {
                    // Best effort: the copy has refcount 1, releasing frees it.
                    let _ = self.store.release(new_obj);
                    let _ = self.store.add_ref(old_obj);
                    self.live[page] = old_obj;
                }
                UndoStep::Overwritten { obj, old_content } => {
                    let _ = self.store.overwrite(obj, &old_content);
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // Snapshots
    // ------------------------------------------------------------------

    /// Fork the live volume into a new snapshot. Copies no page data: only
    /// the mapping vector is cloned and refcounts are incremented.
    pub fn fork(&mut self, name: Option<String>) -> Result<SnapshotInfo> {
        self.op_seq += 1;
        let result = self.fork_inner(name);
        self.quarantine_on_integrity(&result, "fork");
        match &result {
            Ok(info) => self.events.record_ok(
                "fork",
                json!({ "snapshot_id": info.id, "name": info.name,
                        "objects_used": self.store.len(),
                        "total_refs": self.store.total_refs() }),
            ),
            Err(e) => self.events.record_err("fork", e, json!({})),
        }
        result
    }

    // `done` below counts how many refs were taken so the rollback releases
    // exactly those; an explicit counter is clearer than enumerate() here
    // because the loop body needs &mut self for rollback + quarantine.
    #[allow(clippy::explicit_counter_loop)]
    fn fork_inner(&mut self, name: Option<String>) -> Result<SnapshotInfo> {
        self.check_writable()?;
        self.check_refcount_invariant()?;

        let id = self.next_snap_id;
        let name = name.unwrap_or_else(|| format!("snap-{id}"));
        if self.snapshots.values().any(|s| s.name == name) {
            return Err(ServiceError::conflict(
                "snapshot_exists",
                format!("snapshot name {name:?} already exists"),
            ));
        }

        // Increment every referenced object; roll back on anomaly.
        let mut done = 0usize;
        for &obj in &self.live {
            if let Err(e) = self.store.add_ref(obj) {
                for &o in &self.live[..done] {
                    let _ = self.store.release(o);
                }
                self.quarantine(&format!("fork failed at refcount {done}: {e}"));
                return Err(e);
            }
            done += 1;
        }

        let snap = Snapshot {
            name,
            created_seq: self.op_seq,
            mapping: self.live.clone(),
        };
        self.snapshots.insert(id, snap);
        self.next_snap_id += 1;

        if let Err(e) = self.check_refcount_invariant() {
            self.quarantine("refcount invariant broken after fork");
            return Err(e);
        }
        if let Err(e) = self.store.persist().and_then(|_| self.persist_meta()) {
            self.quarantine(&format!("persist failed after fork: {e}"));
            return Err(e);
        }
        Ok(self.snapshot_info(id).expect("snapshot just inserted"))
    }

    /// Delete a snapshot. Only objects no longer referenced by any mapping
    /// are freed; other snapshots and the live volume are unaffected.
    pub fn delete_snapshot(&mut self, id: u64) -> Result<SnapshotInfo> {
        self.op_seq += 1;
        let result = self.delete_snapshot_inner(id);
        self.quarantine_on_integrity(&result, &format!("delete snapshot {id}"));
        match &result {
            Ok(info) => self.events.record_ok(
                "delete_snapshot",
                json!({ "snapshot_id": id, "name": info.name,
                        "objects_used": self.store.len(),
                        "total_refs": self.store.total_refs() }),
            ),
            Err(e) => self
                .events
                .record_err("delete_snapshot", e, json!({ "snapshot_id": id })),
        }
        result
    }

    fn delete_snapshot_inner(&mut self, id: u64) -> Result<SnapshotInfo> {
        self.check_writable()?;
        let info = self.snapshot_info(id).ok_or_else(|| {
            ServiceError::conflict(
                "snapshot_not_found",
                format!("snapshot {id} does not exist"),
            )
        })?;
        let snap = self.snapshots.remove(&id).expect("info existed");

        for &obj in &snap.mapping {
            if let Err(e) = self.store.release(obj) {
                // Partial release: counts no longer match mappings. Quarantine
                // and let the operator repair via audit.
                self.quarantine(&format!(
                    "delete snapshot {id}: release failed mid-way: {e}"
                ));
                let _ = self.store.persist();
                let _ = self.persist_meta();
                return Err(e);
            }
        }

        if let Err(e) = self.check_refcount_invariant() {
            self.quarantine("refcount invariant broken after delete");
            return Err(e);
        }
        if let Err(e) = self.store.persist().and_then(|_| self.persist_meta()) {
            self.quarantine(&format!("persist failed after delete: {e}"));
            return Err(e);
        }
        Ok(info)
    }

    // ------------------------------------------------------------------
    // Reads
    // ------------------------------------------------------------------

    pub fn read_live_page(&self, page: u32) -> Result<Vec<u8>> {
        self.check_page(page)?;
        self.store.read(self.live[page as usize])
    }

    pub fn read_snapshot_page(&self, id: u64, page: u32) -> Result<Vec<u8>> {
        self.check_page(page)?;
        let snap = self.snapshots.get(&id).ok_or_else(|| {
            ServiceError::conflict(
                "snapshot_not_found",
                format!("snapshot {id} does not exist"),
            )
        })?;
        self.store.read(snap.mapping[page as usize])
    }

    pub fn list_snapshots(&self) -> Vec<SnapshotInfo> {
        self.snapshots
            .keys()
            .filter_map(|&id| self.snapshot_info(id))
            .collect()
    }

    pub fn snapshot_info(&self, id: u64) -> Option<SnapshotInfo> {
        let snap = self.snapshots.get(&id)?;
        let shared = snap
            .mapping
            .iter()
            .zip(self.live.iter())
            .filter(|(a, b)| a == b)
            .count();
        Some(SnapshotInfo {
            id,
            name: snap.name.clone(),
            created_seq: snap.created_seq,
            pages_shared_with_live: shared,
            pages_exclusive: self.page_count - shared,
        })
    }

    // ------------------------------------------------------------------
    // Diagnostics
    // ------------------------------------------------------------------

    pub fn stats(&self) -> StatsView {
        StatsView {
            run_id: self.events.run_id().to_string(),
            page_size: self.store.page_size(),
            page_count: self.page_count,
            capacity: self.store.capacity(),
            objects_used: self.store.len(),
            total_refs: self.store.total_refs(),
            expected_total_refs: self.expected_total_refs(),
            snapshots: self.snapshots.len(),
            quarantined: self.quarantine.is_some(),
            quarantine_reason: self.quarantine.clone(),
            stats: self.stats.clone(),
        }
    }

    /// Recompute refcounts from the live + snapshot mappings and compare
    /// against the store table. With `enforce`, a failed audit quarantines
    /// the service.
    pub fn audit(&mut self, enforce: bool) -> Result<AuditReport> {
        let report = self.compute_audit()?;
        if enforce && !report.ok {
            self.quarantine("audit failed (enforced)");
            self.persist_meta()?;
        }
        self.events.record_ok(
            "audit",
            json!({ "ok": report.ok, "enforce": enforce,
                    "mismatches": report.mismatches.len(),
                    "missing_files": report.missing_files.len(),
                    "orphan_files": report.orphan_files.len() }),
        );
        Ok(report)
    }

    fn compute_audit(&self) -> Result<AuditReport> {
        let mut expected: BTreeMap<ObjId, u32> = BTreeMap::new();
        for &obj in self
            .live
            .iter()
            .chain(self.snapshots.values().flat_map(|s| s.mapping.iter()))
        {
            *expected.entry(obj).or_insert(0) += 1;
        }
        let mut mismatches = Vec::new();
        for (&obj, &exp) in &expected {
            let actual = self.store.refcount(obj).unwrap_or(0);
            if actual != exp {
                mismatches.push(RefMismatch {
                    obj,
                    expected: exp,
                    actual,
                });
            }
        }
        // Objects in the table but not referenced anywhere.
        let referenced: std::collections::BTreeSet<ObjId> = expected.keys().copied().collect();
        let mut unreferenced = Vec::new();
        // PageStore exposes counts only via refcount(); iterate via audit of
        // total: any object with a count but no expected entry is a mismatch.
        // We get the full key set through the debug iterator below.
        for (obj, rc) in self.store_objects() {
            if !referenced.contains(&obj) {
                unreferenced.push(RefMismatch {
                    obj,
                    expected: 0,
                    actual: rc,
                });
            }
        }
        mismatches.extend(unreferenced);
        let missing_files = self.store.scan_missing_files();
        let orphan_files = self.store.scan_orphans()?;
        let ok = mismatches.is_empty() && missing_files.is_empty();
        Ok(AuditReport {
            ok,
            expected_total_refs: self.expected_total_refs(),
            actual_total_refs: self.store.total_refs(),
            mismatches,
            missing_files,
            orphan_files,
        })
    }

    fn store_objects(&self) -> Vec<(ObjId, u32)> {
        self.store.snapshot_objects()
    }

    /// Clear quarantine. Only succeeds when a fresh audit is clean — the
    /// operator is expected to repair metadata first.
    pub fn clear_quarantine(&mut self) -> Result<()> {
        let report = self.compute_audit()?;
        if !report.ok {
            return Err(ServiceError::conflict(
                "audit_still_failing",
                format!(
                    "cannot clear quarantine: audit still failing ({} mismatches, {} missing files)",
                    report.mismatches.len(),
                    report.missing_files.len()
                ),
            ));
        }
        self.quarantine = None;
        self.persist_meta()?;
        self.events.record_ok("quarantine.clear", json!({}));
        Ok(())
    }

    pub fn events_recent(&self, limit: usize) -> Vec<Event> {
        self.events.recent(limit)
    }

    pub fn run_id(&self) -> &str {
        self.events.run_id()
    }

    // ------------------------------------------------------------------
    // Test hooks (not part of the public contract)
    // ------------------------------------------------------------------

    /// Directly corrupt a refcount, simulating metadata damage. Used by
    /// integration tests to verify the anomaly-detection contract.
    #[doc(hidden)]
    pub fn debug_set_refcount(&mut self, obj: ObjId, rc: u32) -> Result<()> {
        self.store.debug_set_refcount(obj, rc)
    }

    #[doc(hidden)]
    pub fn debug_refcount(&self, obj: ObjId) -> Option<u32> {
        self.store.refcount(obj)
    }

    /// Object id currently backing a live page (test/diagnostic hook).
    #[doc(hidden)]
    pub fn debug_live_obj(&self, page: usize) -> ObjId {
        self.live[page]
    }

    // ------------------------------------------------------------------
    // Internals
    // ------------------------------------------------------------------

    fn check_page(&self, page: u32) -> Result<()> {
        if page as usize >= self.page_count {
            return Err(ServiceError::input(
                "page_out_of_range",
                format!("page {page} out of range (page_count={})", self.page_count),
            ));
        }
        Ok(())
    }

    fn check_writable(&self) -> Result<()> {
        if let Some(reason) = &self.quarantine {
            return Err(ServiceError::conflict(
                "quarantined",
                format!("service is quarantined, mutations rejected: {reason}"),
            ));
        }
        Ok(())
    }

    fn expected_total_refs(&self) -> u64 {
        self.page_count as u64 * (1 + self.snapshots.len() as u64)
    }

    fn check_refcount_invariant(&self) -> Result<()> {
        let actual = self.store.total_refs();
        let expected = self.expected_total_refs();
        if actual != expected {
            return Err(ServiceError::integrity(
                "refcount_anomaly",
                format!(
                    "refcount invariant violated: sum(refcounts)={actual}, \
                     expected {expected} (page_count={} snapshots={})",
                    self.page_count,
                    self.snapshots.len()
                ),
            ));
        }
        Ok(())
    }

    /// Contract: any Integrity-category failure during a mutation
    /// quarantines the service (see module docs, point 4).
    fn quarantine_on_integrity<T>(&mut self, result: &Result<T>, ctx: &str) {
        if let Err(e) = result {
            if e.category == ErrorCategory::Integrity {
                self.quarantine(&format!("{ctx}: {}", e.message));
                let _ = self.persist_meta();
            }
        }
    }

    fn quarantine(&mut self, reason: &str) {
        if self.quarantine.is_none() {
            self.quarantine = Some(reason.to_string());
            self.events.record_err(
                "quarantine.set",
                &ServiceError::integrity("refcount_anomaly", reason.to_string()),
                json!({}),
            );
        }
    }

    fn persist_meta(&self) -> Result<()> {
        let meta = EngineMeta {
            page_count: self.page_count,
            live: self.live.clone(),
            snapshots: self.snapshots.clone(),
            next_snap_id: self.next_snap_id,
            op_seq: self.op_seq,
            quarantine: self.quarantine.clone(),
            stats: self.stats.clone(),
        };
        let raw = serde_json::to_string_pretty(&meta)
            .map_err(|e| ServiceError::internal("serialize", format!("engine meta: {e}")))?;
        let tmp = self.meta_path.with_extension("tmp");
        fs::write(&tmp, &raw)
            .map_err(|e| ServiceError::internal("io", format!("write engine meta: {e}")))?;
        fs::rename(&tmp, &self.meta_path)
            .map_err(|e| ServiceError::internal("io", format!("rename engine meta: {e}")))?;
        Ok(())
    }
}

fn writes_total(by_page: &BTreeMap<usize, Vec<&PageWrite>>) -> u64 {
    by_page.values().map(|v| v.len() as u64).sum()
}
