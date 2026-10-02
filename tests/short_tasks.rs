//! Short tasks arriving one every 50ms against an always-on background task.
//! Hand-computed references (short-task-storm, duration 1200ms):
//!   - the CPU never idles: the background task is always runnable;
//!   - 20 short tasks * 2ms = 40ms, so background.exec = 1200 - 40 = 1160;
//!   - a short task waits at most one slice of the background task
//!     (slice = max(2, 8*1024/2048) = 4ms) plus quantization, so
//!     finish - arrival <= 4 + 2 + slack = 12ms.

mod common;

use cfs_sim::scenario::find_builtin;
use cfs_sim::task::TaskStatus;
use common::{check_eq, check_in_range, log_step};

const CASE: &str = "short-task-storm";

#[test]
fn short_tasks_are_served_promptly_and_cpu_never_idles() {
    let scenario = find_builtin("short-task-storm").unwrap();
    let (outcome, report) = common::run_scenario(&scenario);

    check_eq(CASE, "elapsed", 1200, outcome.elapsed_ms, "duration bound");
    check_eq(CASE, "idle", 0, outcome.idle_ms,
        "background task is runnable at every tick");
    check_eq(CASE, "busy==elapsed", 1200, outcome.busy_ms, "no idle ticks");

    let background = &outcome.tasks[0];
    check_eq(CASE, "background.exec", 1160, background.stats.exec_ms,
        "1200ms total minus 20*2ms of short-task execution");

    for task in &outcome.tasks[1..] {
        let spec = &task.spec;
        check_eq(CASE, &format!("{}.exec", spec.name), 2, task.stats.exec_ms,
            "each short task runs exactly its 2ms script");
        let finish = task.stats.finish_ms.unwrap_or(u64::MAX);
        let latency = finish.saturating_sub(spec.arrival_ms);
        check_in_range(CASE, &format!("{}.response", spec.name), 2, 12, latency,
            "wait <= one background slice (4ms) + 2ms execution + quantization");
        let done = task.status == TaskStatus::Finished;
        log_step(CASE, &format!("{}.finished", spec.name), "true", &done.to_string(),
            "every short task completes within the run", done);
        assert!(done);
    }
    assert!(report.passed, "all checks must pass: {report:?}");
}

/// Short tasks must not accumulate unbounded vruntime credit by arriving:
/// new tasks are placed at min_vruntime with no bonus, so a burst of
/// arrivals cannot push the background task off the CPU for longer than the
/// burst's own work.
#[test]
fn arrival_burst_does_not_starve_background() {
    let scenario = find_builtin("short-task-storm").unwrap();
    let (outcome, _) = common::run_scenario(&scenario);
    let background = &outcome.tasks[0];
    check_in_range(CASE, "background.max_wait", 0, 16, background.stats.max_wait_ms,
        "arriving tasks are placed at min_vruntime (no bonus); the background \
         task waits at most a couple of slices per arrival");
}
