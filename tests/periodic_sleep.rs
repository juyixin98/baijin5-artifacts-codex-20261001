//! Periodic sleep: blocked time is not execution, and runtime statistics
//! conserve. Hand-computed references for `periodic-sleeper`
//! (run 3ms / sleep 5ms x 100, alone on the CPU):
//!   exec = 100*3 = 300ms, sleep = 100*5 = 500ms, wait = 0,
//!   idle = 500ms (CPU has nothing to do while the task sleeps),
//!   elapsed = 800ms, vruntime = 300 * 2^20 = 314572800.

mod common;

use cfs_sim::scenario::find_builtin;
use common::check_eq;

const CASE: &str = "periodic-sleep";

#[test]
fn blocked_time_is_not_execution_and_stats_conserve() {
    let scenario = find_builtin("periodic-sleeper").unwrap();
    let (outcome, report) = common::run_scenario(&scenario);
    let t = &outcome.tasks[0];

    check_eq(CASE, "exec", 300, t.stats.exec_ms, "100 periods * 3ms run");
    check_eq(CASE, "sleep", 500, t.stats.sleep_ms, "100 periods * 5ms sleep");
    check_eq(CASE, "wait", 0, t.stats.wait_ms,
        "only task on the CPU: runnable means running");
    check_eq(CASE, "idle", 500, outcome.idle_ms,
        "CPU idles exactly while the task sleeps");
    check_eq(CASE, "elapsed", 800, outcome.elapsed_ms, "100 * (3+5)");
    check_eq(CASE, "busy+idle==elapsed", 800, outcome.busy_ms + outcome.idle_ms,
        "global conservation identity");
    check_eq(CASE, "vruntime counts execution only", 314_572_800, t.vruntime,
        "300ms * 1024 * 2^20 / 1024; sleep contributes nothing");
    let finished = t.stats.finish_ms == Some(800);
    common::log_step(CASE, "finish-at-800", "Some(800)",
        &format!("{:?}", t.stats.finish_ms),
        "last run phase ends exactly at t=800", finished);
    assert!(finished);
    assert!(report.passed, "all checks must pass: {report:?}");
}

/// The wakeup after every sleep must place the task at its own vruntime when
/// it is the only task (min_vruntime cannot run ahead of it), so periodic
/// sleepers are not penalized twice.
#[test]
fn sleeper_is_not_penalized_by_placement() {
    let scenario = find_builtin("periodic-sleeper").unwrap();
    let (outcome, _) = common::run_scenario(&scenario);
    let t = &outcome.tasks[0];
    check_eq(CASE, "schedule_count", 100, t.stats.schedule_count,
        "one pick per period: the task is never preempted and never waits");
    check_eq(CASE, "max_wait", 0, t.stats.max_wait_ms,
        "alone on the CPU it runs immediately after every wakeup");
}
