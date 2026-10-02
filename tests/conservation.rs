//! Conservation identities across every builtin scenario, asserted directly
//! on engine output (not via the metrics module, so the two stay honest
//! independently):
//!   per task: exec + wait + sleep == time spent inside the system
//!   global:   sum(exec) + idle == elapsed

mod common;

use cfs_sim::scenario::builtin_scenarios;
use common::{check_eq, log_step};

const CASE: &str = "conservation";

#[test]
fn per_task_and_global_conservation_hold_for_all_builtins() {
    for scenario in builtin_scenarios() {
        let (outcome, _) = common::run_scenario(&scenario);
        for (spec, task) in scenario.tasks.iter().zip(outcome.tasks.iter()) {
            let end = task.stats.finish_ms.unwrap_or(outcome.elapsed_ms);
            let inside = end.saturating_sub(spec.arrival_ms);
            let sum = task.stats.exec_ms + task.stats.wait_ms + task.stats.sleep_ms;
            check_eq(CASE, &format!("{}:{}:exec+wait+sleep", scenario.name, spec.name),
                inside, sum,
                "each tick an arrived, unfinished task is Running, Ready or Sleeping");
        }
        check_eq(CASE, &format!("{}:busy+idle", scenario.name),
            outcome.elapsed_ms, outcome.busy_ms + outcome.idle_ms,
            "each tick exactly one task runs or the CPU idles");
    }
}

/// The metrics layer must reach the same conclusions as the direct
/// assertions above (cross-check of the two independent implementations).
#[test]
fn metrics_layer_agrees_on_conservation() {
    for scenario in builtin_scenarios() {
        let (_, report) = common::run_scenario(&scenario);
        for check in report
            .checks
            .iter()
            .filter(|c| c.category == cfs_sim::metrics::CheckCategory::Conservation)
        {
            log_step(CASE, &format!("metrics:{}", check.name), "pass",
                &check.detail, &check.basis, check.passed);
            assert!(check.passed, "{}", check.name);
        }
    }
}
