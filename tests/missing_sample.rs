//! Missing samples and partial read failures.
//!
//! Fixture (tests/fixtures/missing_sample): snapshot 0003 is absent
//! (sampling gap); at 0004 pid 6000's stat is corrupt (partial read failure)
//! while pid 6001 is gone for good. At 0005 pid 6000 is readable again.
//!
//! Expectations:
//! - the gap makes intervals spanning seq 2..4 indeterminate
//!   (MissingSamples), it does not fabricate deltas;
//! - the corrupt stat marks pid 6000's interval PartialRead but does NOT
//!   declare the process exited;
//! - pid 6000's recovery delta spans the unreadable sample honestly
//!   (from_seq=2, cumulative 150 jiffies);
//! - pid 6001's exit time is uncertain (somewhere in (2, 4]).

mod common;

use common::*;
use procdiff::delta::DeltaClass;
use procdiff::diag::Decision;
use procdiff::engine::ExitMark;

#[test]
fn gap_makes_spanning_interval_indeterminate() {
    let engine = ingest_case("missing_sample");
    // init (pid 1) is readable in every snapshot, so its (2 -> 4) interval
    // isolates the pure gap effect.
    let deltas = deltas_for(&engine, 1);
    let shape: Vec<(u64, u64, DeltaClass, Option<u64>)> = deltas
        .iter()
        .map(|d| (d.from_seq, d.to_seq, d.class, d.cpu_jiffies))
        .collect();
    assert_eq!(
        shape,
        vec![
            (1, 2, DeltaClass::Ok, Some(1)),
            (2, 4, DeltaClass::MissingSamples, None),
            (4, 5, DeltaClass::Ok, Some(1)),
        ]
    );

    let gap = engine
        .state
        .diags
        .iter()
        .find(|r| r.action == "gap")
        .expect("gap diagnostic");
    assert_eq!(gap.decision, Decision::Indeterminate);
    assert!(gap.reason.contains("2 -> 4"));
}

#[test]
fn partial_read_failure_does_not_declare_exit() {
    let engine = ingest_case("missing_sample");
    let survivor = &engine.state.procs[&id(6000, 5)];
    // The process is still considered live despite the corrupt stat at seq 4.
    assert_eq!(survivor.exit, None);

    let deltas = deltas_for(&engine, 6000);
    let shape: Vec<(u64, u64, DeltaClass, Option<u64>)> = deltas
        .iter()
        .map(|d| (d.from_seq, d.to_seq, d.class, d.cpu_jiffies))
        .collect();
    assert_eq!(
        shape,
        vec![
            (1, 2, DeltaClass::Ok, Some(50)),
            (2, 4, DeltaClass::PartialRead, None),
            // Recovery: cumulative delta honestly spans the unreadable sample.
            (2, 5, DeltaClass::Ok, Some(150)),
        ]
    );
    assert!(deltas[2].reason.contains("unreadable"));

    let partial = engine
        .state
        .diags
        .iter()
        .find(|r| r.action == "partial-read" && r.pid == Some(6000))
        .expect("partial-read diagnostic");
    assert_eq!(partial.decision, Decision::Indeterminate);
    assert_eq!(partial.key_state["last_good_seq"], 2);
}

#[test]
fn exit_across_gap_has_uncertain_time() {
    let engine = ingest_case("missing_sample");
    let quitter = &engine.state.procs[&id(6001, 6)];
    // 6001 was alive at seq 2 and gone at seq 4; with seq 3 missing the exit
    // happened somewhere in (2, 4] — recorded as inexact, not pinned to 4.
    assert_eq!(quitter.exit, Some(ExitMark { at_seq: 4, exact: false }));

    let exit = engine
        .state
        .diags
        .iter()
        .find(|r| r.action == "exit" && r.pid == Some(6001))
        .expect("exit diagnostic");
    assert_eq!(exit.decision, Decision::Indeterminate);
}
