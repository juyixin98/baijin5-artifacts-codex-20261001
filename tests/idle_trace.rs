//! CPU idle trace: with no runnable task the CPU idles, idle time is
//! conserved in the totals, and the sampled trace shows the idle ramp.
//! Hand-computed references (idle-trace: one task arrives at t=100, runs
//! 50ms): ticks 0..100 idle, ticks 100..150 busy, run stops at t=150 because
//! every task has finished -> elapsed=150, idle=100, busy=50.

mod common;

use cfs_sim::scenario::find_builtin;
use common::{check_eq, log_step};

const CASE: &str = "idle-trace";

#[test]
fn idle_ticks_are_traced_and_conserved() {
    let scenario = find_builtin("idle-trace").unwrap();
    let (outcome, report) = common::run_scenario(&scenario);
    let t = &outcome.tasks[0];

    check_eq(CASE, "elapsed", 150, outcome.elapsed_ms,
        "engine stops once the only task finishes at t=150");
    check_eq(CASE, "idle", 100, outcome.idle_ms, "no runnable task before t=100");
    check_eq(CASE, "busy", 50, outcome.busy_ms, "the task's 50ms script");
    check_eq(CASE, "exec", 50, t.stats.exec_ms, "script length");
    check_eq(CASE, "wait", 0, t.stats.wait_ms, "alone on the CPU once arrived");
    check_eq(CASE, "busy+idle==elapsed", 150, outcome.busy_ms + outcome.idle_ms,
        "global conservation identity");

    // The sampled trace must show idle_ms climbing to 100 by the arrival
    // and then staying flat while the task runs. A sample recorded at
    // tick_ms=t reflects the state after tick t has executed.
    let at_50 = outcome
        .samples
        .iter()
        .find(|s| s.tick_ms == 50)
        .expect("sample at t=50");
    check_eq(CASE, "sample(t=50).idle", 51, at_50.idle_ms,
        "ticks 0..=50 all idle -> 51 idle ticks counted");
    let at_99 = outcome
        .samples
        .iter()
        .find(|s| s.tick_ms == 99)
        .expect("sample at t=99");
    check_eq(CASE, "sample(t=99).idle", 100, at_99.idle_ms,
        "all 100 ticks before the arrival are idle");
    let last = outcome.samples.last().expect("samples exist");
    check_eq(CASE, "final sample idle", 100, last.idle_ms,
        "idle stops growing once the task arrives");
    let monotonic = outcome
        .samples
        .windows(2)
        .all(|w| w[1].idle_ms >= w[0].idle_ms);
    log_step(CASE, "idle-monotonic", "true", &monotonic.to_string(),
        "idle time can never decrease", monotonic);
    assert!(monotonic);
    assert!(report.passed, "all checks must pass: {report:?}");
}
