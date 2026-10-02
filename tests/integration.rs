//! Scenario tests for the COW snapshot engine.
//!
//! Every scenario mirrors its operations into an independent full-copy
//! reference model (tests/common) and asserts:
//! - concrete snapshot contents (not just "call succeeds"),
//! - concrete physical copy counts (COW vs in-place vs fresh),
//! - concrete error categories for failure cases.
//!
//! Each test writes a JSONL run log to `test-logs/` with its run id,
//! intermediate states, and assertion rationale.

mod common;

use common::{
    RefModel, RunLog, assert_snapshot_matches, open_engine, pattern, test_config,
    TEST_LOGICAL_PAGES,
};
use cow_snap::engine::{CowEngine, WriteReq};
use cow_snap::error::{AppError, ErrorCategory};
use serde_json::json;

fn w(page: usize, offset: usize, data: Vec<u8>) -> WriteReq {
    WriteReq { page, offset, data }
}

#[test]
fn forked_snapshots_diverge_independently() {
    let mut log = RunLog::new("forked_snapshots_diverge_independently");
    let tmp = tempfile::tempdir().unwrap();
    let mut engine = open_engine(tmp.path(), 64);
    let mut model = RefModel::new();

    let base = engine.create_snapshot(None).unwrap();
    assert_eq!(base, model.create(None), "engine and reference ids must stay in sync");

    // Base writes 3 fresh pages: no sharing yet, so 3 fresh allocations.
    let rep = engine
        .write_batch(base, &[w(0, 0, pattern(b'A')), w(1, 0, pattern(b'A')), w(2, 0, pattern(b'A'))])
        .unwrap();
    for p in 0..3 {
        model.write(base, p, 0, &pattern(b'A'));
    }
    assert_eq!((rep.fresh_allocs, rep.cow_copies, rep.in_place_writes), (3, 0, 0));
    assert_eq!(engine.used_pages(), 3);
    log.event("base_written", json!({"used_pages": 3, "report": "3 fresh, 0 cow"}),
              "first writes to never-written pages must allocate fresh zero-based pages, no copies");

    // Fork twice: shares all 3 pages, nothing copied.
    let a = engine.create_snapshot(Some(base)).unwrap();
    assert_eq!(a, model.create(Some(base)));
    let b = engine.create_snapshot(Some(base)).unwrap();
    assert_eq!(b, model.create(Some(base)));
    assert_eq!(engine.used_pages(), 3, "fork must not copy pages");
    log.event("forked", json!({"children": [a, b], "used_pages": 3}),
              "fork shares page references; physical page count unchanged");

    // Child A writes shared page 1: exactly one COW copy.
    let rep = engine.write_batch(a, &[w(1, 0, pattern(b'X'))]).unwrap();
    model.write(a, 1, 0, &pattern(b'X'));
    assert_eq!((rep.cow_copies, rep.in_place_writes), (1, 0));
    assert_eq!(engine.used_pages(), 4);

    // Child B writes shared pages 1 and 2 in one batch: two COW copies.
    let rep = engine.write_batch(b, &[w(1, 0, pattern(b'Y')), w(2, 0, pattern(b'Y'))]).unwrap();
    model.write(b, 1, 0, &pattern(b'Y'));
    model.write(b, 2, 0, &pattern(b'Y'));
    assert_eq!(rep.cow_copies, 2);
    assert_eq!(engine.used_pages(), 6);
    log.event("children_written", json!({"used_pages": 6, "cow_total": engine.counters().cow_copies}),
              "each first write to a shared page copies exactly that page (3 shared -> 3 copies so far)");

    // Child A rewrites page 1, now exclusively owned: in place, no copy.
    let rep = engine.write_batch(a, &[w(1, 0, pattern(b'Z'))]).unwrap();
    model.write(a, 1, 0, &pattern(b'Z'));
    assert_eq!((rep.in_place_writes, rep.cow_copies), (1, 0));
    assert_eq!(engine.used_pages(), 6, "in-place write must not allocate");

    for snap in [base, a, b] {
        assert_snapshot_matches(&engine, &model, snap);
    }
    assert!(engine.verify().ok, "refcounts must be consistent");
    log.event("verified", json!({"snapshots": [base, a, b]}),
              "all three snapshots match the full-copy reference; refcount verify clean");
    log.finish();
}

#[test]
fn overlapping_page_writes_across_siblings() {
    let mut log = RunLog::new("overlapping_page_writes_across_siblings");
    let tmp = tempfile::tempdir().unwrap();
    let mut engine = open_engine(tmp.path(), 64);
    let mut model = RefModel::new();

    let base = engine.create_snapshot(None).unwrap();
    model.create(None);
    engine
        .write_batch(base, &(0..4).map(|p| w(p, 0, pattern(b'B'))).collect::<Vec<_>>())
        .unwrap();
    for p in 0..4 {
        model.write(base, p, 0, &pattern(b'B'));
    }
    let s1 = engine.create_snapshot(Some(base)).unwrap();
    model.create(Some(base));
    let s2 = engine.create_snapshot(Some(base)).unwrap();
    model.create(Some(base));

    // Siblings write overlapping page sets: s1 -> {1,2}, s2 -> {2,3}.
    engine.write_batch(s1, &[w(1, 0, pattern(b'P')), w(2, 0, pattern(b'P'))]).unwrap();
    model.write(s1, 1, 0, &pattern(b'P'));
    model.write(s1, 2, 0, &pattern(b'P'));
    engine.write_batch(s2, &[w(2, 0, pattern(b'Q')), w(3, 0, pattern(b'Q'))]).unwrap();
    model.write(s2, 2, 0, &pattern(b'Q'));
    model.write(s2, 3, 0, &pattern(b'Q'));

    assert_eq!(engine.counters().cow_copies, 4, "4 shared pages written once each");
    assert_eq!(engine.used_pages(), 8, "4 base pages + 4 copied pages");
    log.event("overlap_written", json!({"used_pages": 8, "cow_copies": 4}),
              "overlapping writes to shared pages copy exactly the affected pages; base untouched");

    // Partial-page overlap: splice 4 bytes into the middle of s1's page 2.
    engine.write_batch(s1, &[w(2, 6, vec![0xEE; 4])]).unwrap();
    model.write(s1, 2, 6, &[0xEE; 4]);
    assert_eq!(engine.counters().in_place_writes, 1, "s1 page 2 is exclusive now");

    for snap in [base, s1, s2] {
        assert_snapshot_matches(&engine, &model, snap);
    }
    // Base must still hold the original pattern on the contested pages.
    assert_eq!(engine.read_page(base, 2).unwrap(), pattern(b'B'));
    assert!(engine.verify().ok);
    log.event("verified", json!({"base_page2": "B-pattern intact"}),
              "sibling writes never leak into the base snapshot or each other");
    log.finish();
}

#[test]
fn delete_parent_preserves_children_and_reclaims() {
    let mut log = RunLog::new("delete_parent_preserves_children_and_reclaims");
    let tmp = tempfile::tempdir().unwrap();
    let mut engine = open_engine(tmp.path(), 64);
    let mut model = RefModel::new();

    let base = engine.create_snapshot(None).unwrap();
    model.create(None);
    engine
        .write_batch(base, &(0..3).map(|p| w(p, 0, pattern(b'D'))).collect::<Vec<_>>())
        .unwrap();
    for p in 0..3 {
        model.write(base, p, 0, &pattern(b'D'));
    }
    let child = engine.create_snapshot(Some(base)).unwrap();
    model.create(Some(base));
    let grandchild = engine.create_snapshot(Some(child)).unwrap();
    model.create(Some(child));
    assert_eq!(engine.used_pages(), 3);

    // Delete the middle of the chain: children must be unaffected.
    engine.delete_snapshot(child).unwrap();
    model.delete(child);
    assert_eq!(engine.used_pages(), 3, "all pages still referenced by base and grandchild");
    log.event("middle_deleted", json!({"used_pages": 3}),
              "deleting a parent only drops its own references; shared pages survive via refcount");

    // Delete the base: grandchild keeps all 3 pages.
    engine.delete_snapshot(base).unwrap();
    model.delete(base);
    assert_eq!(engine.used_pages(), 3);
    assert_snapshot_matches(&engine, &model, grandchild);
    log.event("base_deleted", json!({"used_pages": 3, "survivor": grandchild}),
              "grandchild contents identical after both ancestors deleted");

    // Grandchild's pages are now exclusive: rewrite happens in place.
    let rep = engine.write_batch(grandchild, &[w(0, 0, pattern(b'G'))]).unwrap();
    model.write(grandchild, 0, 0, &pattern(b'G'));
    assert_eq!((rep.in_place_writes, rep.cow_copies), (1, 0));
    assert_eq!(engine.used_pages(), 3);

    // Deleting the last snapshot reclaims everything.
    engine.delete_snapshot(grandchild).unwrap();
    model.delete(grandchild);
    assert_eq!(engine.used_pages(), 0, "all physical pages reclaimed");
    assert_eq!(engine.counters().pages_freed, 3);
    assert!(engine.verify().ok);

    // Deleting an unknown snapshot is a state conflict, not an input error.
    let err = engine.delete_snapshot(999).unwrap_err();
    assert_eq!(err.category(), ErrorCategory::StateConflict);
    log.event("reclaimed", json!({"used_pages": 0, "pages_freed": 3}),
              "reclaim is refcount-exact; unknown snapshot delete is a state conflict");
    log.finish();
}

#[test]
fn capacity_exhaustion_rejects_whole_batch_atomically() {
    let mut log = RunLog::new("capacity_exhaustion_rejects_whole_batch_atomically");
    let tmp = tempfile::tempdir().unwrap();
    // Capacity 5: base fills 3, fork shares them, only 2 slots remain.
    let mut engine = open_engine(tmp.path(), 5);
    let mut model = RefModel::new();

    let base = engine.create_snapshot(None).unwrap();
    model.create(None);
    engine
        .write_batch(base, &(0..3).map(|p| w(p, 0, pattern(b'C'))).collect::<Vec<_>>())
        .unwrap();
    for p in 0..3 {
        model.write(base, p, 0, &pattern(b'C'));
    }
    let child = engine.create_snapshot(Some(base)).unwrap();
    model.create(Some(base));
    assert_eq!(engine.used_pages(), 3);

    // Batch needs 3 COW allocations but only 2 are free: reject the
    // WHOLE batch; no page may be partially copied.
    let err = engine
        .write_batch(child, &[w(0, 0, pattern(b'N')), w(1, 0, pattern(b'N')), w(2, 0, pattern(b'N'))])
        .unwrap_err();
    assert_eq!(err.category(), ErrorCategory::ResourceExhausted);
    assert_eq!(engine.used_pages(), 3, "rejected batch must not allocate anything");
    assert_eq!(engine.counters().batches_rejected, 1);
    assert_snapshot_matches(&engine, &model, child);
    log.event("batch_rejected", json!({"used_pages": 3, "needed": 3, "free": 2}),
              "capacity check happens before any allocation: batch is atomic under exhaustion");

    // A batch that fits succeeds: 2 pages -> 2 COW copies, exactly full.
    let rep = engine.write_batch(child, &[w(0, 0, pattern(b'N')), w(1, 0, pattern(b'N'))]).unwrap();
    model.write(child, 0, 0, &pattern(b'N'));
    model.write(child, 1, 0, &pattern(b'N'));
    assert_eq!(rep.cow_copies, 2);
    assert_eq!(engine.used_pages(), 5, "store exactly at capacity");

    // Even a never-written page cannot be allocated now.
    let err = engine.write_batch(child, &[w(7, 0, pattern(b'!'))]).unwrap_err();
    assert_eq!(err.category(), ErrorCategory::ResourceExhausted);
    assert_snapshot_matches(&engine, &model, child);
    assert_snapshot_matches(&engine, &model, base);
    log.event("at_capacity", json!({"used_pages": 5, "capacity": 5}),
              "at-capacity writes fail with resource_exhausted and leave all snapshots intact");

    // Error categories are distinguishable on the same endpoint.
    let input_err = engine.write_batch(child, &[w(99, 0, pattern(b'?'))]).unwrap_err();
    assert_eq!(input_err.category(), ErrorCategory::Input);
    let state_err = engine.write_batch(999, &[w(0, 0, pattern(b'?'))]).unwrap_err();
    assert_eq!(state_err.category(), ErrorCategory::StateConflict);
    log.event("categories", json!({"input": "page 99", "state": "snapshot 999", "resource": "capacity"}),
              "input / state-conflict / resource-exhausted are distinct failure categories");
    log.finish();
}

#[test]
fn batch_input_error_is_atomic() {
    let mut log = RunLog::new("batch_input_error_is_atomic");
    let tmp = tempfile::tempdir().unwrap();
    let mut engine = open_engine(tmp.path(), 16);

    let base = engine.create_snapshot(None).unwrap();
    engine.write_batch(base, &[w(0, 0, pattern(b'K'))]).unwrap();
    let applied_before = engine.counters().batches_applied;

    // One bad element (page index out of range) poisons the whole batch.
    let err = engine
        .write_batch(base, &[w(1, 0, pattern(b'V')), w(TEST_LOGICAL_PAGES, 0, pattern(b'V'))])
        .unwrap_err();
    assert_eq!(err.category(), ErrorCategory::Input);
    assert_eq!(
        engine.read_page(base, 1).unwrap(),
        vec![0u8; common::TEST_PAGE_SIZE],
        "the valid first write of a rejected batch must not be visible"
    );
    assert_eq!(engine.counters().batches_applied, applied_before);
    log.event("atomic_reject", json!({"batches_applied": applied_before}),
              "validation covers the whole batch before mutation: no partial visibility");

    // Offset + length overflow past the page end is also an input error.
    let err = engine.write_batch(base, &[w(0, 14, vec![1, 2, 3])]).unwrap_err();
    assert_eq!(err.category(), ErrorCategory::Input);
    log.finish();
}

#[test]
fn refcount_anomaly_blocks_writes_and_deletes() {
    let mut log = RunLog::new("refcount_anomaly_blocks_writes_and_deletes");
    let tmp = tempfile::tempdir().unwrap();
    let mut engine = open_engine(tmp.path(), 16);

    let base = engine.create_snapshot(None).unwrap();
    engine.write_batch(base, &[w(0, 0, pattern(b'R')), w(1, 0, pattern(b'R'))]).unwrap();
    let child = engine.create_snapshot(Some(base)).unwrap();
    // Phys ids are allocated sequentially from 1: pages 0,1 -> phys 1,2.
    // Corrupt the recorded refcount of phys 1 via the diagnostic hook.
    engine.debug_force_refcount(1, 0);
    log.event("corrupted", json!({"phys": 1, "forced_refcount": 0}),
              "inject refcount anomaly: recorded 0 while two snapshots reference the page");

    // Writes must refuse to continue on the anomalous state.
    let err = engine.write_batch(child, &[w(0, 0, pattern(b'W'))]).unwrap_err();
    assert_eq!(err.category(), ErrorCategory::StateConflict);
    assert!(err.to_string().contains("refcount anomaly"), "got: {err}");
    // Deletes must refuse as well.
    let err = engine.delete_snapshot(base).unwrap_err();
    assert_eq!(err.category(), ErrorCategory::StateConflict);
    // The read-only verifier reports the same anomaly with both counts.
    let report = engine.verify();
    assert!(!report.ok);
    assert_eq!(report.anomalies.len(), 1);
    assert_eq!(report.anomalies[0].recorded, Some(0));
    assert_eq!(report.anomalies[0].recomputed, 2);
    log.event("blocked", json!({"anomaly": report.anomalies[0].recorded, "recomputed": 2}),
              "writes and deletes reject on refcount anomaly; verify() localizes it");
    log.finish();
}

#[test]
fn persistence_roundtrip_restores_exact_state() {
    let mut log = RunLog::new("persistence_roundtrip_restores_exact_state");
    let tmp = tempfile::tempdir().unwrap();
    let mut model = RefModel::new();
    let counters_before;
    let (base, child);
    {
        let mut engine = open_engine(tmp.path(), 16);
        base = engine.create_snapshot(None).unwrap();
        model.create(None);
        engine.write_batch(base, &[w(0, 0, pattern(b'S')), w(3, 0, pattern(b'S'))]).unwrap();
        model.write(base, 0, 0, &pattern(b'S'));
        model.write(base, 3, 0, &pattern(b'S'));
        child = engine.create_snapshot(Some(base)).unwrap();
        model.create(Some(base));
        engine.write_batch(child, &[w(0, 0, pattern(b'T'))]).unwrap();
        model.write(child, 0, 0, &pattern(b'T'));
        counters_before = engine.counters();
    } // engine dropped: only the manifest + page files survive

    let engine = open_engine(tmp.path(), 16);
    assert_eq!(engine.counters(), counters_before, "counters persist across restart");
    assert_eq!(engine.used_pages(), 3, "2 shared + 1 cow page survive restart");
    assert_snapshot_matches(&engine, &model, base);
    assert_snapshot_matches(&engine, &model, child);
    assert!(engine.verify().ok);
    log.event("restored", json!({"used_pages": 3, "cow_copies": counters_before.cow_copies}),
              "manifest + page files fully reconstruct snapshot contents and refcounts");

    // A mismatched geometry against the same data dir is a compute failure.
    let mut bad = test_config(tmp.path(), 16);
    bad.page_size = 32;
    let err = match CowEngine::open(bad) {
        Ok(_) => panic!("mismatched geometry must fail to open"),
        Err(e) => e,
    };
    assert_eq!(err.category(), ErrorCategory::ComputeFailure);
    log.event("config_mismatch", json!({"category": "compute_failure"}),
              "reopening with a different geometry fails as compute_failure, not a silent reset");
    log.finish();
}

#[test]
fn store_rejects_refcount_underflow() {
    // Direct store-level check of the anomaly contract (no engine).
    let tmp = tempfile::tempdir().unwrap();
    let mut store =
        cow_snap::store::PageStore::create(tmp.path().to_path_buf(), 8, 4).unwrap();
    let id = store.alloc_with(&[7u8; 8]).unwrap();
    store.release(id).unwrap();
    let err = store.release(id).unwrap_err();
    assert!(matches!(err, AppError::State(_)), "double release must be a state conflict");
}
