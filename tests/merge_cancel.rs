//! Merge identity preservation and cancel semantics (boundary cases),
//! checked against hand-computed references on the unit HDD.
//!
//! boundary_cancel hand computation (head 0, service = |start-head|+len):
//!   t=0:   c1,c2,c3 arrive; cancel c2 -> cancelled_before_dispatch (cost 0).
//!          dispatch c1 [10,20): service 10+10=20, finish 20.
//!   t=15:  cancel c1 -> in-flight, marked cancel_requested.
//!   t=20:  c1 finishes as cancelled_after_dispatch (cost 20 counted), head 20.
//!          dispatch c3 [200,210): seek 180+10=190, finish 210.
//!   t=210: c3 completes.
//!   t=1000: cancel c3 -> rejected already_finished; cancel ghost -> unknown_id.
//!   Summary: arrived 3, completed 1, cancelled_before 1, cancelled_after 1,
//!   cancel_rejected 2, makespan 210, total service 210, seek distance 190.

mod common;

use blksched::engine::{Engine, EventKind};
use blksched::request::{Direction, FinalStatus};
use blksched::trace::{self, OpJson};
use common::*;

fn arrive(id: &str, start: u64, len: u64) -> OpJson {
    OpJson::Arrive {
        at_ns: 0,
        id: id.to_string(),
        start,
        len,
        direction: Direction::Read,
        deadline_ns: None,
    }
}

#[test]
fn three_way_merge_preserves_all_member_identities_and_completions() {
    let trace = fixture("merge_bridge");
    let out = Engine::new(unit_hdd(), scan(), 0).run(&trace);

    // All four requests merge into a single item [100,140): one dispatch.
    let dispatches: Vec<_> = out
        .events
        .iter()
        .filter(|e| matches!(e.kind, EventKind::Dispatched { .. }))
        .collect();
    assert_eq!(dispatches.len(), 1);
    assert_eq!(dispatches[0].request_ids, vec!["m1", "m2", "m4", "m3"]);

    // service = seek 100 + transfer 40 = 140; every member completes at 140.
    assert_eq!(out.makespan_ns, 140);
    assert_eq!(out.total_service_ns, 140);
    assert_eq!(out.outcomes.len(), 4);
    for o in &out.outcomes {
        assert_eq!(o.status, FinalStatus::Completed);
        assert_eq!(o.finish_ns, 140);
        assert_eq!(o.merged_with.len(), 3, "member {} keeps peer ids", o.id);
    }
    // No identity lost or duplicated.
    let mut ids: Vec<_> = out.outcomes.iter().map(|o| o.id.clone()).collect();
    ids.sort();
    assert_eq!(ids, vec!["m1", "m2", "m3", "m4"]);
}

#[test]
fn cancel_of_merged_interior_member_splits_item() {
    // m1[100,110) m2[110,120) m3[120,130) merge into one item; cancel m2 at
    // t=0 (after arrivals) splits the item back into [100,110) and [120,130).
    let trace = trace::parse_ops(
        "merge_split",
        vec![
            arrive("m1", 100, 10),
            arrive("m2", 110, 10),
            arrive("m3", 120, 10),
            OpJson::Cancel {
                at_ns: 0,
                id: "m2".into(),
            },
        ],
    )
    .unwrap();
    let out = Engine::new(unit_hdd(), scan(), 0).run(&trace);

    let get = |id: &str| out.outcomes.iter().find(|o| o.id == id).unwrap();
    assert_eq!(get("m2").status, FinalStatus::CancelledBeforeDispatch);
    assert_eq!(get("m2").service_ns, 0);
    // m1: seek 100 + 10 = 110 -> finish 110, head 110.
    assert_eq!(get("m1").finish_ns, 110);
    // m3: seek 10 + 10 = 20 -> finish 130.
    assert_eq!(get("m3").finish_ns, 130);
    assert_eq!(out.makespan_ns, 130);
}

#[test]
fn boundary_cancel_trace_matches_hand_computed_reference() {
    let trace = fixture("boundary_cancel");
    let out = Engine::new(unit_hdd(), scan(), 0).run(&trace);

    let get = |id: &str| out.outcomes.iter().find(|o| o.id == id).unwrap();
    assert_eq!(get("c1").status, FinalStatus::CancelledAfterDispatch);
    assert_eq!(get("c1").finish_ns, 20);
    assert_eq!(get("c1").service_ns, 20, "dispatched cost is counted");
    assert_eq!(get("c2").status, FinalStatus::CancelledBeforeDispatch);
    assert_eq!(get("c2").service_ns, 0, "queued cancel is free");
    assert_eq!(get("c3").status, FinalStatus::Completed);
    assert_eq!(get("c3").finish_ns, 210);

    assert_eq!(out.makespan_ns, 210);
    assert_eq!(out.total_service_ns, 210);
    assert_eq!(out.total_seek_distance_sectors, 190);
    assert_eq!(out.cancel_rejected, 2);
    assert_eq!(out.outcomes.len(), 3, "ghost was never a request");

    // Failure categories appear as distinct, explainable events.
    let kinds: Vec<String> = out
        .events
        .iter()
        .map(|e| serde_json::to_value(&e.kind).unwrap()["kind"].to_string())
        .collect();
    assert!(kinds.iter().any(|k| k.contains("cancel_requested")));
    assert!(kinds.iter().any(|k| k.contains("cancelled_after_dispatch")));
    assert!(kinds.iter().any(|k| k.contains("cancelled_before_dispatch")));
    let rejects: Vec<_> = out
        .events
        .iter()
        .filter(|e| matches!(e.kind, EventKind::CancelRejected { .. }))
        .collect();
    assert_eq!(rejects.len(), 2);
    let reasons: Vec<String> = rejects
        .iter()
        .map(|e| serde_json::to_value(&e.kind).unwrap()["reason"].to_string())
        .collect();
    assert!(reasons.iter().any(|r| r.contains("already_finished")));
    assert!(reasons.iter().any(|r| r.contains("unknown_id")));
}

#[test]
fn cancel_at_exact_dispatch_tick_wins_over_dispatch() {
    // c1 arrives at t=0 and is cancelled at t=0: arrivals and cancels are
    // processed before the dispatch decision, so it is never dispatched.
    let trace = trace::parse_ops(
        "tick_tie",
        vec![
            arrive("c1", 10, 10),
            OpJson::Cancel {
                at_ns: 0,
                id: "c1".into(),
            },
        ],
    )
    .unwrap();
    let out = Engine::new(unit_hdd(), scan(), 0).run(&trace);
    assert_eq!(out.outcomes.len(), 1);
    assert_eq!(out.outcomes[0].status, FinalStatus::CancelledBeforeDispatch);
    assert_eq!(out.makespan_ns, 0);
    assert!(out
        .events
        .iter()
        .all(|e| !matches!(e.kind, EventKind::Dispatched { .. })));
}
