//! Deadline anti-starvation rules verified end-to-end through the engine,
//! with hand-computed dispatch orders on the unit HDD.

mod common;

use blksched::engine::{Engine, EventKind};
use blksched::request::Direction;
use blksched::sched::DispatchReason;
use blksched::trace::{self, OpJson};
use common::*;

fn arrive(id: &str, start: u64, len: u64, dir: Direction) -> OpJson {
    OpJson::Arrive {
        at_ns: 0,
        id: id.to_string(),
        start,
        len,
        direction: dir,
        deadline_ns: None,
    }
}

fn dispatches(out: &blksched::engine::EngineOutput) -> Vec<(Vec<String>, DispatchReason)> {
    out.events
        .iter()
        .filter_map(|e| match &e.kind {
            EventKind::Dispatched { reason, .. } => {
                Some((e.request_ids.clone(), reason.clone()))
            }
            _ => None,
        })
        .collect()
}

#[test]
fn expired_read_jumps_the_queue_at_its_expire_time() {
    // r_far [500,510) plus nine near reads Ni [i*20, i*20+10), all at t=0.
    // read_expire = 50. Unit HDD: N1 finishes 30 (head 30), N2 finishes 50
    // (head 50). At t=50 r_far is 50 ns old -> expired -> 3rd dispatch.
    let mut ops = vec![arrive("r_far", 500, 10, Direction::Read)];
    for i in 1..=9u64 {
        ops.push(arrive(&format!("n{i}"), i * 20, 10, Direction::Read));
    }
    let trace = trace::parse_ops("expiry", ops).unwrap();
    let out = Engine::new(unit_hdd(), deadline(50, u64::MAX, 2), 0).run(&trace);

    let d = dispatches(&out);
    assert_eq!(d[0].0, vec!["n1"]);
    assert_eq!(d[1].0, vec!["n2"]);
    assert_eq!(d[2].0, vec!["r_far"], "expired read must jump the queue");
    assert_eq!(
        d[2].1,
        DispatchReason::DeadlineExpired {
            direction: Direction::Read
        },
        "the dispatch reason must explain the anti-starvation rule"
    );
    // All ten requests complete exactly once.
    assert_eq!(out.outcomes.len(), 10);
}

#[test]
fn writes_starved_forces_a_write_after_two_reads() {
    // w1 [1000,1005) write; reads r1..r4 with gaps (no merging). expire never.
    // reads_in_a_row reaches 2 -> the 3rd dispatch is the forced write.
    let ops = vec![
        arrive("w1", 1000, 5, Direction::Write),
        arrive("r1", 10, 5, Direction::Read),
        arrive("r2", 20, 5, Direction::Read),
        arrive("r3", 30, 5, Direction::Read),
        arrive("r4", 40, 5, Direction::Read),
    ];
    let trace = trace::parse_ops("starve", ops).unwrap();
    let out = Engine::new(unit_hdd(), deadline(u64::MAX, u64::MAX, 2), 0).run(&trace);

    let d = dispatches(&out);
    let order: Vec<&str> = d.iter().map(|(ids, _)| ids[0].as_str()).collect();
    assert_eq!(order, vec!["r1", "r2", "w1", "r4", "r3"]);
    assert_eq!(d[2].1, DispatchReason::DeadlineWritesStarved);
    assert_eq!(d[0].1, DispatchReason::ClosestToHead);
    // After the write, the read counter resets and closest-to-head resumes:
    // from head 1005, r4 (dist 960) is closer than r3 (dist 970).
    assert_eq!(d[3].1, DispatchReason::ClosestToHead);
    assert_eq!(out.outcomes.len(), 5);
}

#[test]
fn scan_ignores_expiry_that_deadline_enforces() {
    // Same expiry trace under SCAN: r_far is served in sweep order (last),
    // proving the two schedulers genuinely differ on this input.
    let mut ops = vec![arrive("r_far", 500, 10, Direction::Read)];
    for i in 1..=9u64 {
        ops.push(arrive(&format!("n{i}"), i * 20, 10, Direction::Read));
    }
    let trace = trace::parse_ops("expiry", ops).unwrap();
    let out = Engine::new(unit_hdd(), scan(), 0).run(&trace);
    let d = dispatches(&out);
    assert_eq!(d.last().unwrap().0, vec!["r_far"]);
    assert_eq!(d.len(), 10);
}
