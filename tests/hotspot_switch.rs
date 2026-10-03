//! Hotspot switch trace, hand-computed (capacity 4).
//!
//! Phase 1: pages 1..4 each read twice -> all land in T2.
//! Phase 2: pages 5..8 each read twice. The first pass of the new hotspot
//! misses; the second pass finds 5,6,7 as B1 ghosts, driving p from 0 to 4
//! (recency-favoured), and the new hotspot fully replaces the old one.

mod common;

use arc_cache::arc::Outcome;
use arc_cache::diag::Op;
use arc_cache::engine::{Engine, Request};

use common::{never_fail, pair};

fn read(engine: &mut Engine, page: u64) -> Outcome {
    engine
        .submit(Request {
            request_id: None,
            op: Op::Read,
            page,
            data: None,
        })
        .result
        .expect("read must succeed")
        .outcome
}

#[test]
fn hotspot_switch_adapts_p_and_replaces_residents() {
    let (mut engine, _model, _store) = pair(4, 9, never_fail());

    let mut outcomes = Vec::new();
    let mut p_trajectory = Vec::new();
    for round in 0..2 {
        for page in 1..=4u64 {
            let _ = round;
            outcomes.push(read(&mut engine, page));
            p_trajectory.push(engine.cache().p());
        }
    }
    for _round in 0..2 {
        for page in 5..=8u64 {
            outcomes.push(read(&mut engine, page));
            p_trajectory.push(engine.cache().p());
        }
    }

    assert_eq!(
        outcomes,
        vec![
            // Phase 1: four misses, then four T1 hits.
            Outcome::MissFill,
            Outcome::MissFill,
            Outcome::MissFill,
            Outcome::MissFill,
            Outcome::HitT1,
            Outcome::HitT1,
            Outcome::HitT1,
            Outcome::HitT1,
            // Phase 2: four misses, three B1 ghost hits, one T1 hit.
            Outcome::MissFill,
            Outcome::MissFill,
            Outcome::MissFill,
            Outcome::MissFill,
            Outcome::GhostHitB1,
            Outcome::GhostHitB1,
            Outcome::GhostHitB1,
            Outcome::HitT1,
        ]
    );

    // p stays 0 until the ghost hits, then climbs 1 -> 2 -> 4 (capped at c).
    assert_eq!(
        p_trajectory,
        vec![0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 4, 4]
    );

    let lists = engine.cache().lists();
    assert_eq!(lists.t1, Vec::<u64>::new());
    assert_eq!(lists.t2, vec![5, 6, 7, 8], "new hotspot fully resident");
    assert_eq!(lists.b1, Vec::<u64>::new());
    assert_eq!(lists.b2, vec![1, 2, 3, 4], "old hotspot demoted to ghosts");

    let stats = engine.stats();
    assert_eq!(stats.reads, 16);
    assert_eq!(stats.hits_t1, 5);
    assert_eq!(stats.hits_t2, 0);
    assert_eq!(stats.ghost_hits_b1, 3);
    assert_eq!(stats.ghost_hits_b2, 0);
    assert_eq!(stats.misses, 8);
    assert_eq!(stats.store_fetches, 11);
    assert_eq!(stats.evictions_clean, 7);
    assert_eq!(stats.dropped_t1, 0);
}
