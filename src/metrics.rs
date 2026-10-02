//! Independent verification metrics. This module deliberately does NOT reuse
//! scheduler internals: it re-derives expectations (shares, bounds,
//! conservation identities) from the scenario inputs and checks the engine's
//! observed output against them.

use serde::Serialize;

use crate::config::SchedulerConfig;
use crate::scenario::Scenario;
use crate::scheduler::RunOutcome;
use crate::task::TaskStatus;
use crate::VERSION;

#[derive(Debug, Clone, Serialize, serde::Deserialize)]
pub struct TaskReport {
    pub name: String,
    pub weight: u64,
    pub arrival_ms: u64,
    pub final_status: TaskStatus,
    pub exec_ms: u64,
    pub wait_ms: u64,
    pub sleep_ms: u64,
    pub max_wait_ms: u64,
    pub schedule_count: u64,
    pub finish_ms: Option<u64>,
    /// w_i / sum(w) over the always-runnable cohort, when applicable.
    pub expected_share: Option<f64>,
    /// exec_i / total_exec over the whole run.
    pub actual_share: Option<f64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, serde::Deserialize)]
pub enum CheckCategory {
    Conservation,
    FairShare,
    WaitBound,
    Completion,
}

#[derive(Debug, Clone, Serialize, serde::Deserialize)]
pub struct CheckResult {
    pub name: String,
    pub category: CheckCategory,
    pub passed: bool,
    /// What was observed.
    pub detail: String,
    /// Why the threshold is what it is (判定依据).
    pub basis: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, serde::Deserialize)]
pub enum RunStatus {
    Completed,
    Failed,
}

#[derive(Debug, Clone, Serialize, serde::Deserialize)]
pub struct RunReport {
    pub run_id: String,
    pub version: String,
    pub scenario: String,
    pub status: RunStatus,
    pub failure: Option<String>,
    pub config: SchedulerConfig,
    pub duration_ms: u64,
    pub elapsed_ms: u64,
    pub busy_ms: u64,
    pub idle_ms: u64,
    pub tasks: Vec<TaskReport>,
    pub checks: Vec<CheckResult>,
    pub passed: bool,
}

/// Tasks that are runnable for the whole run: present from t=0 and never
/// sleep. Only for these does the long-term share identity w_i/sum(w) hold.
fn is_always_runnable(spec: &crate::task::TaskSpec) -> bool {
    spec.arrival_ms == 0 && !spec.has_sleep()
}

pub fn build_report(
    run_id: &str,
    scenario: &Scenario,
    outcome: &RunOutcome,
    failure: Option<String>,
) -> RunReport {
    let cfg = scenario.config;
    let cohort_weight: u64 = scenario
        .tasks
        .iter()
        .filter(|s| is_always_runnable(s))
        .map(|s| s.weight)
        .sum();

    let tasks: Vec<TaskReport> = outcome
        .tasks
        .iter()
        .map(|t| {
            let in_cohort = is_always_runnable(&t.spec) && cohort_weight > 0;
            let expected_share = in_cohort.then_some(t.spec.weight as f64 / cohort_weight as f64);
            let actual_share = (outcome.busy_ms > 0)
                .then_some(t.stats.exec_ms as f64 / outcome.busy_ms as f64);
            TaskReport {
                name: t.spec.name.clone(),
                weight: t.spec.weight,
                arrival_ms: t.spec.arrival_ms,
                final_status: t.status,
                exec_ms: t.stats.exec_ms,
                wait_ms: t.stats.wait_ms,
                sleep_ms: t.stats.sleep_ms,
                max_wait_ms: t.stats.max_wait_ms,
                schedule_count: t.stats.schedule_count,
                finish_ms: t.stats.finish_ms,
                expected_share,
                actual_share,
            }
        })
        .collect();

    let mut checks = Vec::new();
    checks.extend(conservation_checks(scenario, outcome));
    if scenario.checks.shares {
        checks.extend(share_checks(scenario, outcome, cohort_weight));
    }
    if scenario.checks.wait_bound {
        checks.extend(wait_bound_checks(scenario, outcome));
    }
    if scenario.checks.expect_all_finished {
        checks.push(completion_check(outcome));
    }

    let status = if failure.is_some() {
        RunStatus::Failed
    } else {
        RunStatus::Completed
    };
    let passed = status == RunStatus::Completed && checks.iter().all(|c| c.passed);
    RunReport {
        run_id: run_id.to_string(),
        version: VERSION.to_string(),
        scenario: scenario.name.clone(),
        status,
        failure,
        config: cfg,
        duration_ms: scenario.duration_ms,
        elapsed_ms: outcome.elapsed_ms,
        busy_ms: outcome.busy_ms,
        idle_ms: outcome.idle_ms,
        tasks,
        checks,
        passed,
    }
}

/// Per-task: exec + wait + sleep == time spent inside the system.
/// Global: sum(exec) + idle == elapsed. Blocked time is never execution.
fn conservation_checks(scenario: &Scenario, outcome: &RunOutcome) -> Vec<CheckResult> {
    let mut checks = Vec::new();
    for (spec, task) in scenario.tasks.iter().zip(outcome.tasks.iter()) {
        let end = task.stats.finish_ms.unwrap_or(outcome.elapsed_ms);
        let inside = end.saturating_sub(spec.arrival_ms);
        let sum = task.stats.exec_ms + task.stats.wait_ms + task.stats.sleep_ms;
        let passed = sum == inside;
        checks.push(CheckResult {
            name: format!("conservation:{}", spec.name),
            category: CheckCategory::Conservation,
            passed,
            detail: format!(
                "exec={} + wait={} + sleep={} = {} vs in-system={} (arrival={}, end={})",
                task.stats.exec_ms, task.stats.wait_ms, task.stats.sleep_ms, sum, inside,
                spec.arrival_ms, end
            ),
            basis: "every tick an arrived, unfinished task is in exactly one of \
                    Running/Ready/Sleeping, so the three counters must sum to \
                    the time spent inside the system"
                .to_string(),
        });
    }
    let total = outcome.busy_ms + outcome.idle_ms;
    checks.push(CheckResult {
        name: "conservation:cpu".to_string(),
        category: CheckCategory::Conservation,
        passed: total == outcome.elapsed_ms,
        detail: format!(
            "busy={} + idle={} = {} vs elapsed={}",
            outcome.busy_ms, outcome.idle_ms, total, outcome.elapsed_ms
        ),
        basis: "each tick exactly one task runs or the CPU is idle".to_string(),
    });
    checks
}

/// Long-term CPU share of the always-runnable cohort must approach w_i/sum(w).
fn share_checks(
    scenario: &Scenario,
    outcome: &RunOutcome,
    cohort_weight: u64,
) -> Vec<CheckResult> {
    let tol = scenario.checks.share_tolerance;
    let mut checks = Vec::new();
    if cohort_weight == 0 || outcome.busy_ms == 0 {
        checks.push(CheckResult {
            name: "fair-share".to_string(),
            category: CheckCategory::FairShare,
            passed: false,
            detail: "no always-runnable cohort or no busy time; share check not applicable"
                .to_string(),
            basis: "share identity w_i/sum(w) requires a non-empty always-runnable cohort"
                .to_string(),
        });
        return checks;
    }
    for (spec, task) in scenario
        .tasks
        .iter()
        .zip(outcome.tasks.iter())
        .filter(|(s, _)| is_always_runnable(s))
    {
        let expected = spec.weight as f64 / cohort_weight as f64;
        let actual = task.stats.exec_ms as f64 / outcome.busy_ms as f64;
        let dev = (actual - expected).abs();
        checks.push(CheckResult {
            name: format!("fair-share:{}", spec.name),
            category: CheckCategory::FairShare,
            passed: dev <= tol,
            detail: format!(
                "expected={:.4} actual={:.4} dev={:.4} (exec={} of busy={})",
                expected, actual, dev, task.stats.exec_ms, outcome.busy_ms
            ),
            basis: format!(
                "CFS long-term identity: share_i -> w_i/sum(w) = {}/{}; tolerance {}",
                spec.weight, cohort_weight, tol
            ),
        });
    }
    checks
}

/// Wait bound applies only when the cohort is stable: all tasks present from
/// t=0, none sleeping, no mid-window arrivals. Then every runnable task must
/// be scheduled within one scheduling period
/// max(target_latency, n * min_granularity), plus one tick of quantization.
fn wait_bound_checks(scenario: &Scenario, outcome: &RunOutcome) -> Vec<CheckResult> {
    let cfg = &scenario.config;
    let cohort: Vec<_> = scenario
        .tasks
        .iter()
        .zip(outcome.tasks.iter())
        .filter(|(s, _)| is_always_runnable(s))
        .collect();
    let stable = cohort.len() == scenario.tasks.len();
    if !stable {
        return vec![CheckResult {
            name: "wait-bound".to_string(),
            category: CheckCategory::WaitBound,
            passed: false,
            detail: "scenario has sleepers or late arrivals; bound not applicable"
                .to_string(),
            basis: "the period bound assumes a stable always-runnable cohort; \
                    arrivals and wakeups reset the window"
                .to_string(),
        }];
    }
    let n = cohort.len() as u64;
    let bound = cfg.period_bound_ms(n) + cfg.tick_ms;
    cohort
        .into_iter()
        .map(|(spec, task)| CheckResult {
            name: format!("wait-bound:{}", spec.name),
            category: CheckCategory::WaitBound,
            passed: task.stats.max_wait_ms <= bound,
            detail: format!(
                "max_wait={}ms vs bound={}ms (n={})",
                task.stats.max_wait_ms, bound, n
            ),
            basis: format!(
                "period = max(target_latency={}ms, n*min_granularity={}*{}ms) + tick={}ms",
                cfg.target_latency_ms, n, cfg.min_granularity_ms, cfg.tick_ms
            ),
        })
        .collect()
}

fn completion_check(outcome: &RunOutcome) -> CheckResult {
    let unfinished: Vec<_> = outcome
        .tasks
        .iter()
        .filter(|t| t.status != TaskStatus::Finished)
        .map(|t| t.spec.name.clone())
        .collect();
    CheckResult {
        name: "completion".to_string(),
        category: CheckCategory::Completion,
        passed: unfinished.is_empty(),
        detail: if unfinished.is_empty() {
            "all tasks finished their scripts".to_string()
        } else {
            format!("unfinished: {}", unfinished.join(", "))
        },
        basis: "scenario declares expect_all_finished".to_string(),
    }
}
