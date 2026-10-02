//! Reference test: the `tiny` fixture run through SCAN and deadline, checked
//! against hand-computed values (device: unit HDD, service = |start-head|+len).
//!
//! Hand computation (head starts at 0):
//!   r4 [10,15):  seek 10 + 5  = 15  -> finish 15,  head 15
//!   r2 [50,60):  seek 35 + 10 = 45  -> finish 60,  head 60
//!   r1 [100,110): seek 40 + 10 = 50 -> finish 110, head 110
//!   r3 [200,210): seek 90 + 10 = 100 -> finish 210, head 210
//!   makespan 210, total seek distance 10+35+40+90 = 175, total service 210.
//!   r3 has deadline 50 ns (relative to arrival 0) and finishes at 210 -> miss.

mod common;

use blksched::engine::{Engine, EventKind};
use blksched::request::FinalStatus;
use common::*;

fn dispatch_order(out: &blksched::engine::EngineOutput) -> Vec<Vec<String>> {
    out.events
        .iter()
        .filter(|e| matches!(e.kind, EventKind::Dispatched { .. }))
        .map(|e| e.request_ids.clone())
        .collect()
}

#[test]
fn tiny_scan_matches_hand_computed_reference() {
    let trace = fixture("tiny");
    let out = Engine::new(unit_hdd(), scan(), 0).run(&trace);

    assert_eq!(
        dispatch_order(&out),
        vec![vec!["r4"], vec!["r2"], vec!["r1"], vec!["r3"]]
    );
    let finish = |id: &str| {
        out.outcomes
            .iter()
            .find(|o| o.id == id)
            .unwrap()
            .finish_ns
    };
    assert_eq!(finish("r4"), 15);
    assert_eq!(finish("r2"), 60);
    assert_eq!(finish("r1"), 110);
    assert_eq!(finish("r3"), 210);
    assert_eq!(out.makespan_ns, 210);
    assert_eq!(out.total_service_ns, 210);
    assert_eq!(out.total_seek_distance_sectors, 175);
    assert_eq!(out.head_final_sector, 210);
    assert!(out
        .outcomes
        .iter()
        .all(|o| o.status == FinalStatus::Completed));
    // r3 is the only deadline miss (deadline 50, finished 210).
    let misses: Vec<&str> = out
        .outcomes
        .iter()
        .filter(|o| o.deadline_miss)
        .map(|o| o.id.as_str())
        .collect();
    assert_eq!(misses, vec!["r3"]);
}

#[test]
fn tiny_deadline_without_expiry_matches_same_reference() {
    let trace = fixture("tiny");
    let out = Engine::new(unit_hdd(), deadline_no_expire(), 0).run(&trace);
    // With no expiry pressure, closest-to-head reproduces the same order here.
    assert_eq!(
        dispatch_order(&out),
        vec![vec!["r4"], vec!["r2"], vec!["r1"], vec!["r3"]]
    );
    assert_eq!(out.makespan_ns, 210);
    assert_eq!(out.total_seek_distance_sectors, 175);
}

#[test]
fn tiny_on_ssd_like_has_zero_seek_and_carries_caveat() {
    let trace = fixture("tiny");
    let device = unit_ssd_like();
    let out = Engine::new(device.clone(), scan(), 0).run(&trace);
    // service = 100 + len; finishes: r4 105, r2 215, r1 325, r3 435.
    assert_eq!(out.makespan_ns, 435);
    assert_eq!(out.total_seek_ns, 0);
    assert_eq!(out.total_seek_distance_sectors, 0);
    assert_eq!(out.total_service_ns, 105 + 110 + 110 + 110);
    // The model must not be presentable as an SSD measurement.
    assert!(device.caveat().contains("NOT an SSD measurement"));
}
