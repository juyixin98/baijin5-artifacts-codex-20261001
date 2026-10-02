//! Wait bound: for a stable always-runnable cohort, every task is scheduled
//! within one scheduling period max(target_latency, n*min_granularity) plus
//! one tick. The bound's applicability conditions (no sleepers, no late
//! arrivals) are tested too: when they are violated the check must report
//! "not applicable" (fail-closed), not silently pass.

mod common;

use cfs_sim::config::SchedulerConfig;
use cfs_sim::metrics::CheckCategory;
use cfs_sim::scenario::{find_builtin, CheckSpec, Scenario};
use cfs_sim::task::{Phase, TaskSpec};
use common::{check_in_range, log_step};

const CASE: &str = "wait-bound";

#[test]
fn equal_trio_max_wait_within_period_bound() {
    let scenario = find_builtin("equal-trio").unwrap();
    let (outcome, _) = common::run_scenario(&scenario);
    // period = max(8, 3*2) = 8; bound = 8 + 1 tick = 9ms.
    for task in &outcome.tasks {
        check_in_range(CASE, &format!("{}.max_wait", task.spec.name), 0, 9,
            task.stats.max_wait_ms,
            "period = max(target_latency=8, n*min_gran=3*2) + tick = 9ms");
    }
}

#[test]
fn fair_weights_max_wait_within_period_bound() {
    let scenario = find_builtin("fair-weights-1-3").unwrap();
    let (outcome, _) = common::run_scenario(&scenario);
    // n=2 -> period = max(8, 2*2) = 8; bound = 9ms. The light task waits out
    // the heavy task's 6ms slice, so the tight expectation is ~6ms.
    let light = &outcome.tasks[0];
    check_in_range(CASE, "light.max_wait", 0, 9, light.stats.max_wait_ms,
        "light waits at most one heavy slice (6ms) < bound 9ms");
    let heavy = &outcome.tasks[1];
    check_in_range(CASE, "heavy.max_wait", 0, 9, heavy.stats.max_wait_ms,
        "heavy waits at most one light slice (2ms) < bound 9ms");
}

#[test]
fn bound_is_not_applied_when_cohort_is_unstable() {
    // Same shape as periodic-sleeper but with the wait bound force-enabled:
    // the sleeper violates the applicability conditions, so the check must
    // come back failed-with-explanation rather than vacuously passing.
    let scenario = Scenario {
        name: "misapplied-bound".into(),
        description: "sleeper with wait_bound forced on".into(),
        duration_ms: 100,
        config: SchedulerConfig::default(),
        checks: CheckSpec {
            shares: false,
            wait_bound: true,
            expect_all_finished: false,
            share_tolerance: 0.02,
        },
        tasks: vec![TaskSpec {
            name: "sleeper".into(),
            weight: 1024,
            arrival_ms: 0,
            script: vec![Phase::Run { ms: 3 }, Phase::Sleep { ms: 5 }],
            repeats: 12,
        }],
    };
    let (_, report) = common::run_scenario(&scenario);
    let bound_checks: Vec<_> = report
        .checks
        .iter()
        .filter(|c| c.category == CheckCategory::WaitBound)
        .collect();
    assert_eq!(bound_checks.len(), 1, "exactly one applicability verdict");
    let check = bound_checks[0];
    log_step(CASE, "unstable-cohort", "fail-closed",
        &format!("passed={} detail={}", check.passed, check.detail),
        "sleepers/arrivals reset the window; the period bound does not apply",
        !check.passed);
    assert!(!check.passed, "bound must not be reported as holding");
    assert!(check.detail.contains("not applicable"));
    // And the report as a whole must not claim success.
    assert!(!report.passed);
}
