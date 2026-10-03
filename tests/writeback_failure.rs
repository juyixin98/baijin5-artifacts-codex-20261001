//! Dirty-page eviction through the write-back adapter, including failure
//! traces. Failure semantics (README §6): the whole access is rejected with
//! a `writeback` error and the cache state is left untouched.

mod common;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use arc_cache::arc::{Class, Outcome};
use arc_cache::diag::Op;
use arc_cache::engine::{Engine, Request};
use arc_cache::error::ErrorCategory;

use common::pair;

fn write(engine: &mut Engine, page: u64, data: Vec<u8>) -> Outcome {
    engine
        .submit(Request {
            request_id: None,
            op: Op::Write,
            page,
            data: Some(data),
        })
        .result
        .expect("write must succeed")
        .outcome
}

fn read(engine: &mut Engine, page: u64) -> arc_cache::engine::Response {
    engine.submit(Request {
        request_id: None,
        op: Op::Read,
        page,
        data: None,
    })
}

#[test]
fn failed_writeback_aborts_access_and_preserves_state() {
    let fail = Arc::new(AtomicBool::new(true));
    let fail2 = fail.clone();
    // Fail write-backs of page 1 while `fail` is set.
    let script = Arc::new(move |page: u64| page == 1 && fail2.load(Ordering::SeqCst));
    let (mut engine, _model, store) = pair(2, 4, script);

    assert_eq!(write(&mut engine, 1, vec![0xA1; 4]), Outcome::MissFill);
    assert_eq!(write(&mut engine, 2, vec![0xB2; 4]), Outcome::MissFill);
    assert_eq!(engine.cache().dirty_pages(), vec![1, 2]);

    // Read of 3 must evict dirty page 1 (T1 is full, B1 empty -> paper case
    // IV(i) else-branch: drop T1 LRU entirely). Write-back fails.
    let resp = read(&mut engine, 3);
    let err = resp.result.expect_err("write-back failure must reject the read");
    assert_eq!(err.category(), ErrorCategory::Writeback);
    assert!(err.to_string().contains("page 1"));

    // State is exactly as before the failed access.
    let cache = engine.cache();
    assert_eq!(cache.lists().t1, vec![1, 2]);
    assert_eq!(cache.dirty_pages(), vec![1, 2]);
    assert_eq!(cache.classify(3), Class::Absent);
    assert_eq!(cache.stats().writeback_failures, 1);
    assert_eq!(cache.stats().writebacks, 0);
    assert_eq!(
        store.get(1),
        Some(common::fixture_content(1)),
        "failed write-back must not persist the dirty data"
    );

    // Heal the adapter and retry: the same access now succeeds, page 1 is
    // written back and dropped entirely (no ghost entry in this branch).
    fail.store(false, Ordering::SeqCst);
    let resp = read(&mut engine, 3);
    let ok = resp.result.expect("read succeeds after adapter heals");
    assert_eq!(ok.outcome, Outcome::MissFill);
    assert_eq!(store.get(1), Some(vec![0xA1; 4]));
    let cache = engine.cache();
    assert_eq!(cache.classify(1), Class::Absent, "dropped T1 page leaves no ghost");
    assert_eq!(cache.lists().t1, vec![2, 3]);
    assert_eq!(cache.dirty_pages(), vec![2], "page 2 is still dirty");
    assert_eq!(cache.stats().writebacks, 1);
    assert_eq!(cache.stats().dropped_t1, 1);
    assert_eq!(cache.stats().evictions_dirty, 1);
}

#[test]
fn dirty_victim_in_replace_goes_to_ghost_after_writeback() {
    let fail = Arc::new(AtomicBool::new(true));
    let fail2 = fail.clone();
    let script = Arc::new(move |page: u64| page == 2 && fail2.load(Ordering::SeqCst));
    let (mut engine, _model, store) = pair(2, 4, script);

    write(&mut engine, 1, vec![0x11; 4]);
    write(&mut engine, 2, vec![0x22; 4]);
    // Promote page 1 to T2 so page 2 becomes the T1 LRU victim.
    let ok = read(&mut engine, 1).result.expect("hit");
    assert_eq!(ok.outcome, Outcome::HitT1);

    // Read 3: REPLACE picks T1 LRU = dirty page 2. Write-back fails.
    let resp = read(&mut engine, 3);
    let err = resp.result.expect_err("write-back failure rejects the access");
    assert_eq!(err.category(), ErrorCategory::Writeback);
    let cache = engine.cache();
    assert_eq!(cache.lists().t1, vec![2]);
    assert_eq!(cache.lists().t2, vec![1]);
    assert_eq!(cache.dirty_pages(), vec![1, 2]);
    assert_eq!(cache.stats().writeback_failures, 1);

    // Heal and retry: page 2 is written back, then lands in B1 as a ghost.
    fail.store(false, Ordering::SeqCst);
    let ok = read(&mut engine, 3).result.expect("read succeeds");
    assert_eq!(ok.outcome, Outcome::MissFill);
    assert_eq!(store.get(2), Some(vec![0x22; 4]), "dirty content persisted");
    let cache = engine.cache();
    assert_eq!(cache.classify(2), Class::B1);
    assert!(!cache.contains_data(2), "ghost holds no data");
    assert_eq!(cache.dirty_pages(), vec![1]);

    // Re-reading page 2 is a ghost hit: content comes from the store and is
    // exactly what the write-back adapter persisted.
    let ok = read(&mut engine, 2).result.expect("ghost hit read");
    assert_eq!(ok.outcome, Outcome::GhostHitB1);
    assert_eq!(ok.data.unwrap(), vec![0x22; 4]);
}

#[test]
fn resize_failure_is_atomic() {
    let fail = Arc::new(AtomicBool::new(true));
    let fail2 = fail.clone();
    let script = Arc::new(move |page: u64| page == 1 && fail2.load(Ordering::SeqCst));
    let (mut engine, _model, _store) = pair(2, 4, script);

    write(&mut engine, 1, vec![0xAA; 4]);
    write(&mut engine, 2, vec![0xBB; 4]);

    let err = engine.resize(1).expect_err("dirty victim write-back fails");
    assert_eq!(err.category(), ErrorCategory::Writeback);
    // Capacity and cache state are unchanged.
    assert_eq!(engine.cache().capacity(), 2);
    assert_eq!(engine.cache().lists().t1, vec![1, 2]);
    assert_eq!(engine.cache().dirty_pages(), vec![1, 2]);

    fail.store(false, Ordering::SeqCst);
    engine.resize(1).expect("resize succeeds after healing");
    assert_eq!(engine.cache().capacity(), 1);
    assert_eq!(engine.cache().lists().t1, vec![2]);
    assert_eq!(engine.cache().dirty_pages(), vec![2]);
}
