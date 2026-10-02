//! Scheduler-independent invariants over seeded random and fixture traces:
//! no request is ever lost or duplicated, timing is monotone, and the cost
//! accounting is consistent. These hold for ANY correct implementation, so
//! they complement the hand-computed reference tests.

mod common;

use blksched::engine::Engine;
use blksched::request::FinalStatus;
use blksched::trace::{self, TraceOp};
use common::*;
use std::collections::HashSet;

fn check_conservation(trace: &blksched::trace::Trace, out: &blksched::engine::EngineOutput) {
    let arrived_ids: HashSet<&str> = trace
        .ops
        .iter()
        .filter_map(|op| match op {
            TraceOp::Arrive(r) => Some(r.id.as_str()),
            _ => None,
        })
        .collect();
    // Every arrived request ends with exactly one outcome: no loss, no dup.
    let outcome_ids: HashSet<&str> = out.outcomes.iter().map(|o| o.id.as_str()).collect();
    assert_eq!(
        arrived_ids, outcome_ids,
        "outcome identities must equal arrived identities"
    );
    assert_eq!(out.outcomes.len(), arrived_ids.len());

    for o in &out.outcomes {
        assert!(o.finish_ns >= o.arrival_ns, "{} finishes before arrival", o.id);
        if let Some(d) = o.dispatch_ns {
            assert!(d >= o.arrival_ns, "{} dispatched before arrival", o.id);
            assert!(o.finish_ns > d, "{} finishes before dispatch ends", o.id);
        } else {
            assert_eq!(o.status, FinalStatus::CancelledBeforeDispatch);
            assert_eq!(o.service_ns, 0);
        }
        // Deadline-miss flag is consistent with the recorded timestamps.
        if o.status == FinalStatus::Completed {
            if let Some(dl) = o.deadline_ns {
                assert_eq!(o.deadline_miss, o.finish_ns > o.arrival_ns + dl);
            }
        } else {
            assert!(!o.deadline_miss);
        }
    }

    // Device busy time cannot exceed the busy span.
    assert!(out.total_service_ns <= out.makespan_ns || out.makespan_ns == 0);
    // Event log timestamps are non-decreasing.
    for w in out.events.windows(2) {
        assert!(w[0].t_ns <= w[1].t_ns, "event log goes backwards in time");
    }
}

#[test]
fn random_traces_conserve_requests_on_both_schedulers_and_devices() {
    for seed in 1..=10u64 {
        let trace = trace::random_trace(seed, 64);
        for make_sched in [scan as fn() -> _, deadline_no_expire as fn() -> _] {
            for device in [unit_hdd(), unit_ssd_like()] {
                let out = Engine::new(device, make_sched(), 0).run(&trace);
                check_conservation(&trace, &out);
            }
        }
    }
}

#[test]
fn mixed_rw_fixture_conserves_requests_on_both_schedulers() {
    let trace = fixture("mixed_rw");
    for make_sched in [scan as fn() -> _, deadline_no_expire as fn() -> _] {
        let out = Engine::new(unit_hdd(), make_sched(), 0).run(&trace);
        check_conservation(&trace, &out);
        assert_eq!(out.outcomes.len(), 24);
    }
}

#[test]
fn sequential_fixture_merges_into_one_contiguous_item() {
    // 16 contiguous reads [0,256): both schedulers merge them into a single
    // dispatch; every member still gets its own completion.
    let trace = fixture("sequential");
    for make_sched in [scan as fn() -> _, deadline_no_expire as fn() -> _] {
        let out = Engine::new(unit_hdd(), make_sched(), 0).run(&trace);
        let dispatch_count = out
            .events
            .iter()
            .filter(|e| matches!(e.kind, blksched::engine::EventKind::Dispatched { .. }))
            .count();
        assert_eq!(dispatch_count, 1);
        assert_eq!(out.outcomes.len(), 16);
        // Unit HDD, head 0: service = seek 0 + transfer 256 = 256.
        assert_eq!(out.makespan_ns, 256);
        assert!(out.outcomes.iter().all(|o| o.finish_ns == 256));
    }
}
