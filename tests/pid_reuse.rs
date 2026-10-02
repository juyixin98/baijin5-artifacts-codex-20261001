//! PID rapid reuse: a new generation of the same PID must not inherit the
//! previous generation's cumulative counters.
//!
//! Fixture (tests/fixtures/pid_reuse): pid 2000 gen1 (start=100) reports
//! utime 10 -> 25 -> 45 over seq 1..3; at seq 4 the pid reappears as gen2
//! (start=900) with utime 5, growing to 15 at seq 5.

mod common;

use common::*;
use procdiff::delta::DeltaClass;
use procdiff::diag::Decision;
use procdiff::engine::ExitMark;

#[test]
fn reused_pid_does_not_inherit_counters() {
    let engine = ingest_case("pid_reuse");
    let deltas = deltas_for(&engine, 2000);

    // Hand-computed expectation: gen1 deltas 15 and 20 jiffies, then a Reset
    // boundary, then gen2's own delta of 10 jiffies. If counters leaked
    // across generations, the (3->4) interval would show 5 - 45 (a bogus
    // regression) or gen2 would start from 45.
    let shape: Vec<(u64, u64, DeltaClass, Option<u64>)> = deltas
        .iter()
        .map(|d| (d.from_seq, d.to_seq, d.class, d.cpu_jiffies))
        .collect();
    assert_eq!(
        shape,
        vec![
            (1, 2, DeltaClass::Ok, Some(15)),
            (2, 3, DeltaClass::Ok, Some(20)),
            (3, 4, DeltaClass::Reset, None),
            (4, 5, DeltaClass::Ok, Some(10)),
        ]
    );

    // Deltas belong to the right generations.
    assert_eq!(deltas[0].identity, id(2000, 100));
    assert_eq!(deltas[2].identity, id(2000, 900));
    assert_eq!(deltas[3].identity, id(2000, 900));
}

#[test]
fn old_generation_is_closed_with_uncertain_exit_time() {
    let engine = ingest_case("pid_reuse");
    let gen1 = &engine.state.procs[&id(2000, 100)];
    // The old generation vanished between samples; its exit time cannot be
    // pinned exactly, only bounded by the reuse observation at seq 4.
    assert_eq!(gen1.exit, Some(ExitMark { at_seq: 4, exact: false }));

    let gen2 = &engine.state.procs[&id(2000, 900)];
    assert_eq!(gen2.exit, None);
    assert_eq!(gen2.last_cpu, 15);
}

#[test]
fn generation_reset_is_diagnosed() {
    let engine = ingest_case("pid_reuse");
    let reset = engine
        .state
        .diags
        .iter()
        .find(|r| r.action == "generation-reset" && r.pid == Some(2000))
        .expect("generation-reset diagnostic");
    assert_eq!(reset.decision, Decision::Accepted);
    assert!(reset.request_id.starts_with("req-"));
    assert!(reset.reason.contains("start_time=900"));
    // The uncertain exit of gen1 is reported as indeterminate.
    let exit = engine
        .state
        .diags
        .iter()
        .find(|r| r.action == "exit" && r.pid == Some(2000))
        .expect("exit diagnostic");
    assert_eq!(exit.decision, Decision::Indeterminate);
}
