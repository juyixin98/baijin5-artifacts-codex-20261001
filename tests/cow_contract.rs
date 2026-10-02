//! Contract tests for the CoW snapshot engine.
//!
//! Every test drives the engine and the independent `FullCopyRef` reference
//! through the same operations and compares page-for-page. Expected copy
//! counts and object counts are hardcoded from first principles (see the
//! comments), never derived from the engine under test.

mod common;

use common::*;
use cow_snapshot_service::engine::Engine;
use cow_snapshot_service::error::ErrorCategory;

const PAGE: usize = 64;

/// Fork, then write: only the touched, still-shared pages are copied.
#[test]
fn fork_then_write_copies_only_touched_pages() {
    let cfg = test_cfg(test_dir("fork_then_write"), PAGE, 8, 64);
    let mut engine = Engine::init_or_open(&cfg).unwrap();
    let mut ref_ = FullCopyRef::new(PAGE, 8);
    tlog!(engine, "init: objects_used={}", engine.stats().objects_used);
    assert_eq!(
        engine.stats().objects_used,
        1,
        "fresh volume is one shared zero object"
    );

    // Batch 1: pages 1,2,3 — all point at the shared zero object => 3 copies.
    let w1 = [
        full_page_write(1, 0xA1, PAGE),
        full_page_write(2, 0xA2, PAGE),
        full_page_write(3, 0xA3, PAGE),
    ];
    let r1 = engine.batch_write(to_page_writes(&w1)).unwrap();
    ref_.batch_write(&w1);
    tlog!(
        engine,
        "batch1: copied={} in_place={} objects={}",
        r1.pages_copied,
        r1.pages_in_place,
        r1.objects_used
    );
    assert_eq!(
        (r1.pages_touched, r1.pages_copied, r1.pages_in_place),
        (3, 3, 0)
    );
    assert_eq!(r1.objects_used, 4, "zero object + 3 new page objects");

    let s1 = engine.fork(Some("s1".into())).unwrap();
    ref_.fork(s1.id, "s1");
    assert_eq!(
        engine.stats().objects_used,
        4,
        "fork copies no page objects"
    );

    // Batch 2: pages 2,3,5 — each object is shared with s1 => 3 copies.
    let w2 = [
        full_page_write(2, 0xB2, PAGE),
        full_page_write(3, 0xB3, PAGE),
        full_page_write(5, 0xB5, PAGE),
    ];
    let r2 = engine.batch_write(to_page_writes(&w2)).unwrap();
    ref_.batch_write(&w2);
    tlog!(
        engine,
        "batch2: copied={} in_place={} objects={}",
        r2.pages_copied,
        r2.pages_in_place,
        r2.objects_used
    );
    assert_eq!(
        (r2.pages_touched, r2.pages_copied, r2.pages_in_place),
        (3, 3, 0)
    );
    assert_eq!(r2.objects_used, 7, "3 more page objects, none freed");

    assert_matches_reference(&engine, &ref_, "after fork+write");
    assert_eq!(engine.stats().stats.pages_copied_total, 6);
    assert!(engine.audit(false).unwrap().ok);
}

/// A page whose object is exclusively owned by the live volume is written
/// in place: no copy, no new object.
#[test]
fn exclusive_page_write_is_in_place() {
    let cfg = test_cfg(test_dir("exclusive_inplace"), PAGE, 4, 16);
    let mut engine = Engine::init_or_open(&cfg).unwrap();
    let mut ref_ = FullCopyRef::new(PAGE, 4);

    let w1 = [full_page_write(0, 0x01, PAGE)];
    let r1 = engine.batch_write(to_page_writes(&w1)).unwrap();
    ref_.batch_write(&w1);
    assert_eq!(
        (r1.pages_copied, r1.pages_in_place),
        (1, 0),
        "first write copies the shared zero page"
    );
    assert_eq!(r1.objects_used, 2);

    let w2 = [full_page_write(0, 0x02, PAGE)];
    let r2 = engine.batch_write(to_page_writes(&w2)).unwrap();
    ref_.batch_write(&w2);
    tlog!(
        engine,
        "second write: copied={} in_place={} objects={}",
        r2.pages_copied,
        r2.pages_in_place,
        r2.objects_used
    );
    assert_eq!(
        (r2.pages_copied, r2.pages_in_place),
        (0, 1),
        "exclusive object is overwritten in place"
    );
    assert_eq!(r2.objects_used, 2, "in-place write allocates nothing");

    assert_matches_reference(&engine, &ref_, "in-place");
}

/// Two snapshots forked at different times see their own versions of
/// repeatedly overwritten pages; deleting one never disturbs the other.
#[test]
fn overlapping_writes_across_snapshots() {
    let cfg = test_cfg(test_dir("overlapping_writes"), PAGE, 8, 64);
    let mut engine = Engine::init_or_open(&cfg).unwrap();
    let mut ref_ = FullCopyRef::new(PAGE, 8);

    let w = [
        full_page_write(0, 0x10, PAGE),
        full_page_write(1, 0x11, PAGE),
        full_page_write(2, 0x12, PAGE),
    ];
    engine.batch_write(to_page_writes(&w)).unwrap();
    ref_.batch_write(&w);
    let s1 = engine.fork(Some("a".into())).unwrap();
    ref_.fork(s1.id, "a");

    let w = [full_page_write(1, 0x21, PAGE)];
    engine.batch_write(to_page_writes(&w)).unwrap();
    ref_.batch_write(&w);
    let s2 = engine.fork(Some("b".into())).unwrap();
    ref_.fork(s2.id, "b");

    let w = [
        full_page_write(1, 0x31, PAGE),
        full_page_write(2, 0x32, PAGE),
    ];
    let r = engine.batch_write(to_page_writes(&w)).unwrap();
    ref_.batch_write(&w);
    assert_eq!(r.pages_copied, 2, "both pages shared with snapshots");
    assert_eq!(
        r.objects_used, 7,
        "zero + p0 + p1(s1) + p1(s2) + p1(live) + p2(s1&s2) + p2(live)"
    );

    // Each viewer sees its own version of page 1.
    assert_eq!(
        engine.read_snapshot_page(s1.id, 1).unwrap(),
        pattern(0x11, PAGE)
    );
    assert_eq!(
        engine.read_snapshot_page(s2.id, 1).unwrap(),
        pattern(0x21, PAGE)
    );
    assert_eq!(engine.read_live_page(1).unwrap(), pattern(0x31, PAGE));
    assert_matches_reference(&engine, &ref_, "overlapping writes");

    // Deleting s1 frees exactly its unique page-1 object (7 -> 6).
    engine.delete_snapshot(s1.id).unwrap();
    ref_.delete(s1.id);
    let st = engine.stats();
    tlog!(engine, "after delete s1: objects={}", st.objects_used);
    assert_eq!(
        st.objects_used, 6,
        "only s1's exclusive object is reclaimed"
    );
    assert_matches_reference(&engine, &ref_, "after deleting s1");
    assert!(engine.audit(false).unwrap().ok);
}

/// Deleting a parent snapshot must not reclaim objects still referenced by
/// a later sibling or by the live volume.
#[test]
fn delete_parent_snapshot_reclaims_only_unreferenced() {
    let cfg = test_cfg(test_dir("delete_parent"), PAGE, 8, 64);
    let mut engine = Engine::init_or_open(&cfg).unwrap();
    let mut ref_ = FullCopyRef::new(PAGE, 8);

    let w: Vec<_> = (0..4)
        .map(|p| full_page_write(p, 0x40 + p as u8, PAGE))
        .collect();
    engine.batch_write(to_page_writes(&w)).unwrap();
    ref_.batch_write(&w);
    assert_eq!(
        engine.stats().objects_used,
        5,
        "zero object + 4 page objects"
    );

    let s1 = engine.fork(Some("parent".into())).unwrap();
    ref_.fork(s1.id, "parent");
    let s2 = engine.fork(Some("child".into())).unwrap();
    ref_.fork(s2.id, "child");

    // Deleting the parent frees nothing: every object is still referenced
    // by the child snapshot and the live volume.
    engine.delete_snapshot(s1.id).unwrap();
    ref_.delete(s1.id);
    assert_eq!(
        engine.stats().objects_used,
        5,
        "parent deletion reclaims nothing still referenced"
    );
    assert_matches_reference(&engine, &ref_, "parent deleted");

    // Overwrite pages 0..3 in live: 4 copies (shared with child).
    let w: Vec<_> = (0..4)
        .map(|p| full_page_write(p, 0x50 + p as u8, PAGE))
        .collect();
    let r = engine.batch_write(to_page_writes(&w)).unwrap();
    ref_.batch_write(&w);
    assert_eq!(r.pages_copied, 4);
    assert_eq!(engine.stats().objects_used, 9);

    // Deleting the child now frees exactly the 4 old page objects.
    engine.delete_snapshot(s2.id).unwrap();
    ref_.delete(s2.id);
    let st = engine.stats();
    tlog!(engine, "after delete child: objects={}", st.objects_used);
    assert_eq!(
        st.objects_used, 5,
        "child's 4 unreferenced objects reclaimed"
    );
    assert_matches_reference(&engine, &ref_, "child deleted");
    assert!(engine.audit(false).unwrap().ok);
}

/// A batch that would exceed capacity fails atomically: error category is
/// ResourceExhausted, no page is copied, no mapping changes, and the
/// service keeps accepting writes that do fit.
#[test]
fn capacity_exhaustion_is_atomic() {
    let cfg = test_cfg(test_dir("capacity_exhaustion"), 32, 4, 6);
    let mut engine = Engine::init_or_open(&cfg).unwrap();
    let mut ref_ = FullCopyRef::new(32, 4);

    let w = [full_page_write(0, 0x60, 32), full_page_write(1, 0x61, 32)];
    engine.batch_write(to_page_writes(&w)).unwrap();
    ref_.batch_write(&w);
    let s1 = engine.fork(None).unwrap();
    ref_.fork(s1.id, "snap-1");
    assert_eq!(engine.stats().objects_used, 3, "zero + 2 page objects");

    // All 4 pages are shared with the snapshot => needs 4 new objects,
    // only 6-3=3 available => the whole batch must be rejected.
    let w: Vec<_> = (0..4)
        .map(|p| full_page_write(p, 0x70 + p as u8, 32))
        .collect();
    let err = engine.batch_write(to_page_writes(&w)).unwrap_err();
    tlog!(engine, "capacity error: {err}");
    assert_eq!(err.category, ErrorCategory::ResourceExhausted);
    assert_eq!(err.code, "capacity_exhausted");

    // Nothing changed: object count, contents, and service health.
    assert_eq!(
        engine.stats().objects_used,
        3,
        "rejected batch copied nothing"
    );
    assert_matches_reference(&engine, &ref_, "after rejected batch");

    // A smaller batch that fits still succeeds (no poisoning by the failure).
    let w = [full_page_write(0, 0x80, 32)];
    let r = engine.batch_write(to_page_writes(&w)).unwrap();
    ref_.batch_write(&w);
    assert_eq!((r.pages_copied, r.objects_used), (1, 4));
    assert_matches_reference(&engine, &ref_, "after recovery write");
    assert!(engine.audit(false).unwrap().ok);
}

/// A corrupted refcount is detected on the write path; the service
/// quarantines and rejects all further mutations until the metadata is
/// repaired and the quarantine explicitly cleared.
#[test]
fn refcount_anomaly_quarantines_writes() {
    let cfg = test_cfg(test_dir("refcount_anomaly"), PAGE, 4, 16);
    let mut engine = Engine::init_or_open(&cfg).unwrap();
    let mut ref_ = FullCopyRef::new(PAGE, 4);

    let w = [full_page_write(0, 0x90, PAGE)];
    engine.batch_write(to_page_writes(&w)).unwrap();
    ref_.batch_write(&w);
    let victim = engine.debug_live_obj(0);
    assert_eq!(engine.debug_refcount(victim), Some(1));

    // Simulate metadata corruption: refcount 1 -> 5.
    engine.debug_set_refcount(victim, 5).unwrap();
    tlog!(engine, "injected corruption: obj {victim} refcount 1 -> 5");

    let w = [full_page_write(1, 0x91, PAGE)];
    let err = engine.batch_write(to_page_writes(&w)).unwrap_err();
    tlog!(engine, "write after corruption: {err}");
    assert_eq!(err.category, ErrorCategory::Integrity);
    assert_eq!(err.code, "refcount_anomaly");

    // Quarantined: every mutation is rejected with StateConflict/quarantined.
    assert!(engine.stats().quarantined);
    let err = engine.batch_write(to_page_writes(&w)).unwrap_err();
    assert_eq!(
        (err.category, err.code),
        (ErrorCategory::StateConflict, "quarantined")
    );
    let err = engine.fork(None).unwrap_err();
    assert_eq!(
        (err.category, err.code),
        (ErrorCategory::StateConflict, "quarantined")
    );

    // Audit reports the mismatch; clearing without repair is refused.
    let report = engine.audit(false).unwrap();
    assert!(!report.ok);
    assert!(report
        .mismatches
        .iter()
        .any(|m| m.obj == victim && m.expected == 1 && m.actual == 5));
    let err = engine.clear_quarantine().unwrap_err();
    assert_eq!(
        (err.category, err.code),
        (ErrorCategory::StateConflict, "audit_still_failing")
    );

    // Repair the metadata, clear, and the service accepts writes again.
    engine.debug_set_refcount(victim, 1).unwrap();
    engine.clear_quarantine().unwrap();
    assert!(!engine.stats().quarantined);
    let r = engine.batch_write(to_page_writes(&w)).unwrap();
    ref_.batch_write(&w);
    assert_eq!(r.pages_copied, 1);
    assert_matches_reference(&engine, &ref_, "after repair");
    assert!(engine.audit(false).unwrap().ok);
}

/// Input errors and state conflicts are distinct categories, and neither
/// poisons subsequent valid operations.
#[test]
fn input_validation_and_state_conflicts() {
    let cfg = test_cfg(test_dir("input_validation"), 32, 4, 16);
    let mut engine = Engine::init_or_open(&cfg).unwrap();

    let cases: Vec<(&str, cow_snapshot_service::engine::PageWrite, &'static str)> = vec![
        (
            "page out of range",
            cow_snapshot_service::engine::PageWrite {
                page: 9,
                offset: 0,
                data: vec![1],
            },
            "page_out_of_range",
        ),
        (
            "offset+len overflow",
            cow_snapshot_service::engine::PageWrite {
                page: 0,
                offset: 30,
                data: vec![0; 4],
            },
            "write_out_of_bounds",
        ),
        (
            "empty data",
            cow_snapshot_service::engine::PageWrite {
                page: 0,
                offset: 0,
                data: vec![],
            },
            "empty_write",
        ),
    ];
    for (name, w, code) in cases {
        let err = engine.batch_write(vec![w]).unwrap_err();
        tlog!(engine, "input case {name}: {err}");
        assert_eq!(err.category, ErrorCategory::Input, "{name}");
        assert_eq!(err.code, code, "{name}");
    }
    let err = engine.batch_write(vec![]).unwrap_err();
    assert_eq!(
        (err.category, err.code),
        (ErrorCategory::Input, "empty_batch")
    );

    engine.fork(Some("dup".into())).unwrap();
    let err = engine.fork(Some("dup".into())).unwrap_err();
    assert_eq!(
        (err.category, err.code),
        (ErrorCategory::StateConflict, "snapshot_exists")
    );
    let err = engine.delete_snapshot(999).unwrap_err();
    assert_eq!(
        (err.category, err.code),
        (ErrorCategory::StateConflict, "snapshot_not_found")
    );
    let err = engine.read_snapshot_page(999, 0).unwrap_err();
    assert_eq!(
        (err.category, err.code),
        (ErrorCategory::StateConflict, "snapshot_not_found")
    );
    let err = engine.read_live_page(9).unwrap_err();
    assert_eq!(
        (err.category, err.code),
        (ErrorCategory::Input, "page_out_of_range")
    );

    // None of the failures affected state.
    let w = [full_page_write(0, 0xA0, 32)];
    engine.batch_write(to_page_writes(&w)).unwrap();
    assert_eq!(engine.read_live_page(0).unwrap(), pattern(0xA0, 32));
    assert!(engine.audit(false).unwrap().ok);
}

/// State survives a restart: contents, snapshots, and stats are reloaded
/// from the data directory; the new process gets a fresh run id.
#[test]
fn persistence_restart() {
    let dir = test_dir("persistence_restart");
    let cfg = test_cfg(dir.clone(), PAGE, 8, 64);
    let mut ref_ = FullCopyRef::new(PAGE, 8);
    let first_run_id;
    let snap_id;

    {
        let mut engine = Engine::init_or_open(&cfg).unwrap();
        first_run_id = engine.run_id().to_string();
        let w = [
            full_page_write(0, 0xC0, PAGE),
            full_page_write(1, 0xC1, PAGE),
        ];
        engine.batch_write(to_page_writes(&w)).unwrap();
        ref_.batch_write(&w);
        let s = engine.fork(Some("before-restart".into())).unwrap();
        snap_id = s.id;
        ref_.fork(s.id, "before-restart");
        let w = [full_page_write(0, 0xC2, PAGE)];
        engine.batch_write(to_page_writes(&w)).unwrap();
        ref_.batch_write(&w);
        tlog!(
            engine,
            "pre-restart: objects={}",
            engine.stats().objects_used
        );
    }

    {
        let mut engine = Engine::init_or_open(&cfg).unwrap();
        assert_ne!(
            engine.run_id(),
            first_run_id,
            "new process run gets a new run id"
        );
        assert_eq!(engine.stats().stats.batches_committed, 2, "stats persisted");
        assert_eq!(engine.list_snapshots().len(), 1);
        assert_matches_reference(&engine, &ref_, "after restart");

        // The volume keeps working: writes after restart still CoW correctly.
        let w = [full_page_write(1, 0xC3, PAGE)];
        let r = engine.batch_write(to_page_writes(&w)).unwrap();
        ref_.batch_write(&w);
        assert_eq!(r.pages_copied, 1, "page 1 still shared with the snapshot");
        assert_eq!(
            engine.read_snapshot_page(snap_id, 1).unwrap(),
            pattern(0xC1, PAGE)
        );
        assert_matches_reference(&engine, &ref_, "write after restart");
        assert!(engine.audit(false).unwrap().ok);
    }
}

/// Deterministic pseudo-random operation sequence cross-checked against the
/// full-copy reference after every single operation.
#[test]
fn randomized_cross_check_against_full_copy() {
    let cfg = test_cfg(test_dir("randomized_cross_check"), PAGE, 16, 256);
    let mut engine = Engine::init_or_open(&cfg).unwrap();
    let mut ref_ = FullCopyRef::new(PAGE, 16);

    // xorshift64* — deterministic, no external crates.
    let mut rng = 0x9E3779B97F4A7C15u64;
    let mut next = move || {
        rng ^= rng >> 12;
        rng ^= rng << 25;
        rng ^= rng >> 27;
        rng.wrapping_mul(0x2545F4914F6CDD1D)
    };

    let mut next_snap_id = 1u64;
    for op in 0..200u32 {
        match next() % 10 {
            // 60%: batch write of 1..=4 pages at random offsets.
            0..=5 => {
                let n = 1 + (next() % 4) as usize;
                let writes: Vec<(u32, u32, Vec<u8>)> = (0..n)
                    .map(|_| {
                        let page = (next() % 16) as u32;
                        let offset = (next() % PAGE as u64) as u32;
                        let len = 1 + (next() % (PAGE as u64 - offset as u64)) as usize;
                        let data: Vec<u8> = (0..len).map(|_| next() as u8).collect();
                        (page, offset, data)
                    })
                    .collect();
                let report = engine.batch_write(to_page_writes(&writes)).unwrap();
                ref_.batch_write(&writes);
                // Copy count is bounded by the number of touched pages and
                // never touches unwritten pages.
                assert!(report.pages_copied <= report.pages_touched);
                assert_eq!(
                    report.pages_copied + report.pages_in_place,
                    report.pages_touched
                );
            }
            // 20%: fork (cap at 5 snapshots).
            6..=7 => {
                if ref_.snaps.len() < 5 {
                    let info = engine.fork(None).unwrap();
                    assert_eq!(info.id, next_snap_id);
                    ref_.fork(info.id, &info.name);
                    next_snap_id += 1;
                }
            }
            // 20%: delete a random existing snapshot.
            _ => {
                if !ref_.snaps.is_empty() {
                    let ids: Vec<u64> = ref_.snaps.keys().copied().collect();
                    let id = ids[(next() as usize) % ids.len()];
                    engine.delete_snapshot(id).unwrap();
                    ref_.delete(id);
                }
            }
        }
        assert_matches_reference(&engine, &ref_, &format!("op {op}"));
        // Refcount invariant holds after every operation.
        let st = engine.stats();
        assert_eq!(st.total_refs, st.expected_total_refs, "op {op}");
    }

    let st = engine.stats();
    tlog!(
        engine,
        "final: objects={} snapshots={} batches={} copied_total={} in_place_total={}",
        st.objects_used,
        st.snapshots,
        st.stats.batches_committed,
        st.stats.pages_copied_total,
        st.stats.pages_in_place_total
    );
    assert!(engine.audit(false).unwrap().ok);
}
