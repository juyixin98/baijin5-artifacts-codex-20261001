//! Capacity zero and dynamic resize semantics (README §5, §7).

mod common;

use arc_cache::arc::Outcome;
use arc_cache::diag::Op;
use arc_cache::engine::{Engine, Request};
use arc_cache::error::ErrorCategory;

use common::{fixture_content, never_fail, pair};

fn read(engine: &mut Engine, page: u64) -> arc_cache::engine::Response {
    engine.submit(Request {
        request_id: None,
        op: Op::Read,
        page,
        data: None,
    })
}

fn write(engine: &mut Engine, page: u64, data: Vec<u8>) -> arc_cache::engine::Response {
    engine.submit(Request {
        request_id: None,
        op: Op::Write,
        page,
        data: Some(data),
    })
}

#[test]
fn zero_capacity_is_read_and_write_through() {
    let (mut engine, _model, store) = pair(0, 6, never_fail());

    let ok = read(&mut engine, 5).result.expect("read-through succeeds");
    assert_eq!(ok.outcome, Outcome::ReadThrough);
    assert_eq!(ok.data.unwrap(), fixture_content(5));
    // Nothing is cached, ever.
    assert_eq!(engine.cache().resident_len(), 0);
    assert_eq!(engine.cache().lists().t1, Vec::<u64>::new());

    // Second read goes to the store again (no caching happened).
    let loads = store.load_count();
    read(&mut engine, 5).result.expect("read-through again");
    assert_eq!(store.load_count(), loads + 1);

    // Writes are pushed straight through the adapter.
    let ok = write(&mut engine, 5, vec![0x5A; 8]).result.expect("write-through");
    assert_eq!(ok.outcome, Outcome::WriteThrough);
    assert_eq!(store.get(5), Some(vec![0x5A; 8]));
    assert_eq!(engine.cache().dirty_pages(), Vec::<u64>::new());

    let stats = engine.stats();
    assert_eq!(stats.read_throughs, 2);
    assert_eq!(stats.write_throughs, 1);
    assert_eq!(stats.misses, 0, "zero capacity never fills the cache");
    assert_eq!(stats.hits_t1 + stats.hits_t2, 0);

    // Growing from zero is a normal resize.
    engine.resize(3).expect("grow from zero");
    let ok = read(&mut engine, 5).result.expect("now cached");
    assert_eq!(ok.outcome, Outcome::MissFill);
    let ok = read(&mut engine, 5).result.expect("now hits");
    assert_eq!(ok.outcome, Outcome::HitT1);
}

#[test]
fn shrink_evicts_and_write_backs_dirty_pages() {
    let (mut engine, _model, store) = pair(3, 6, never_fail());
    for page in 1..=3u64 {
        write(&mut engine, page, vec![page as u8; 4])
            .result
            .expect("write");
    }
    assert_eq!(engine.cache().dirty_pages(), vec![1, 2, 3]);

    engine.resize(1).expect("shrink");
    let cache = engine.cache();
    assert_eq!(cache.lists().t1, vec![3]);
    assert_eq!(cache.dirty_pages(), vec![3], "only the survivor stays dirty");
    assert_eq!(cache.p(), 0);
    // Victims were written back with their dirty content, then dropped from
    // the ghost lists (B1 is trimmed to the new capacity).
    assert_eq!(store.get(1), Some(vec![1u8; 4]));
    assert_eq!(store.get(2), Some(vec![2u8; 4]));
    assert_eq!(cache.lists().b1, Vec::<u64>::new());
    assert_eq!(cache.stats().writebacks, 2);
    assert_eq!(cache.stats().evictions_dirty, 2);

    // Shrinking to zero empties everything.
    engine.resize(0).expect("shrink to zero");
    let cache = engine.cache();
    assert_eq!(cache.resident_len(), 0);
    assert_eq!(cache.dirty_pages(), Vec::<u64>::new());
    assert_eq!(store.get(3), Some(vec![3u8; 4]));
    assert_eq!(cache.p(), 0);
    let ok = read(&mut engine, 3).result.expect("read-through at c=0");
    assert_eq!(ok.outcome, Outcome::ReadThrough);
}

#[test]
fn grow_then_misses_fill_free_slots_before_evicting() {
    // Grow corner (README §5 rule 5): after a capacity increase with ghosts
    // present, a miss must NOT evict a resident while free slots remain.
    let (mut engine, _model, _store) = pair(2, 8, never_fail());
    // Warm 1,2 into T2, then miss 3,4 so ghosts exist in both B1 and B2.
    for page in [1u64, 2, 1, 2, 3, 4] {
        read(&mut engine, page).result.expect("warm");
    }
    let cache = engine.cache();
    assert_eq!(cache.lists().b1, vec![3]);
    assert_eq!(cache.lists().b2, vec![1]);
    assert_eq!(cache.resident_len(), 2);
    engine.resize(4).expect("grow");
    let ok = read(&mut engine, 5).result.expect("miss into free slot");
    assert_eq!(ok.outcome, Outcome::MissFill);
    let cache = engine.cache();
    assert!(
        cache.contains_data(2) && cache.contains_data(4) && cache.contains_data(5),
        "no resident may be evicted while free capacity exists"
    );
    assert_eq!(cache.stats().evictions_clean, 2, "only the pre-grow evictions");
}

#[test]
fn store_io_failure_is_undecidable_and_leaves_state_untouched() {
    let (mut engine, _model, store) = pair(2, 6, never_fail());
    read(&mut engine, 1).result.expect("warm");
    store.set_fail_loads(true);

    let resp = read(&mut engine, 2);
    let err = resp.result.expect_err("store failure");
    assert_eq!(err.category(), ErrorCategory::Store);

    // The diagnostic decision is "undecidable", not a rejection.
    let diags = engine.diagnostics(1, None);
    let record = diags[0];
    assert!(matches!(
        record.decision,
        arc_cache::diag::Decision::Undecidable { .. }
    ));

    // Cache state unchanged by the failed access.
    assert_eq!(engine.cache().lists().t1, vec![1]);
    assert_eq!(engine.cache().stats().misses, 1);

    store.set_fail_loads(false);
    let ok = read(&mut engine, 2).result.expect("works again");
    assert_eq!(ok.outcome, Outcome::MissFill);
}
