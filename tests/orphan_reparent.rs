//! Orphan re-parenting: when a parent exits, its children are re-parented
//! (here to init, pid 1). The parent history must preserve the temporal
//! relationship — who was the parent during which sequence range.
//!
//! Fixture (tests/fixtures/orphan_reparent): 3000 (parent) and 3001 (child)
//! exist at seq 1..2; 3000 is gone at seq 3 and 3001 reports ppid=1.

mod common;

use common::*;
use procdiff::delta::DeltaClass;
use procdiff::engine::ExitMark;
use procdiff::tree::{ParentSpan, TreeEvent};

#[test]
fn parent_history_preserves_time_ranges() {
    let engine = ingest_case("orphan_reparent");
    let child = &engine.state.procs[&id(3001, 60)];
    assert_eq!(
        child.parent_history,
        vec![
            ParentSpan { ppid: 3000, from_seq: 1, to_seq: Some(2) },
            ParentSpan { ppid: 1, from_seq: 3, to_seq: None },
        ]
    );

    let reparent = engine
        .state
        .events
        .iter()
        .find(|e| matches!(e, TreeEvent::Reparented { .. }))
        .expect("reparent event");
    assert_eq!(
        *reparent,
        TreeEvent::Reparented {
            identity: id(3001, 60),
            from_ppid: 3000,
            to_ppid: 1,
            at_seq: 3,
        }
    );
}

#[test]
fn parent_exit_is_exact_at_snapshot_boundary() {
    let engine = ingest_case("orphan_reparent");
    let parent = &engine.state.procs[&id(3000, 50)];
    assert_eq!(parent.exit, Some(ExitMark { at_seq: 3, exact: true }));
}

#[test]
fn tree_reflects_parent_before_and_after_reparenting() {
    let engine = ingest_case("orphan_reparent");

    let before = engine.tree_at(2);
    assert_eq!(before[&id(3001, 60)].ppid, Some(3000));
    assert!(before[&id(3000, 50)].children.contains(&id(3001, 60)));

    let after = engine.tree_at(4);
    assert_eq!(after[&id(3001, 60)].ppid, Some(1));
    assert!(after[&id(1, 0)].children.contains(&id(3001, 60)));
    // The exited parent is no longer a tree node.
    assert!(!after.contains_key(&id(3000, 50)));
}

#[test]
fn child_deltas_continue_across_reparenting() {
    let engine = ingest_case("orphan_reparent");
    let deltas = deltas_for(&engine, 3001);
    let shape: Vec<(u64, u64, DeltaClass, Option<u64>)> = deltas
        .iter()
        .map(|d| (d.from_seq, d.to_seq, d.class, d.cpu_jiffies))
        .collect();
    // utime 3 -> 6 -> 10 -> 15: re-parenting must not disturb accounting.
    assert_eq!(
        shape,
        vec![
            (1, 2, DeltaClass::Ok, Some(3)),
            (2, 3, DeltaClass::Ok, Some(4)),
            (3, 4, DeltaClass::Ok, Some(5)),
        ]
    );
}
