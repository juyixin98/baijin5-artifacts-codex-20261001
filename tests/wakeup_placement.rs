//! Wakeup placement: a task waking from a long sleep gets at most
//! `wakeup_granularity` of vruntime credit behind min_vruntime, so it cannot
//! preempt the others indefinitely.
//!
//! Hand-computed constants (default config, weight 1024):
//!   bonus_v = 2ms * 1024 * 2^20 / 1024 = 2097152 vruntime units.
//! Scenario: H always-on (Run 2000ms); S runs 1ms, sleeps 500ms, runs 400ms.
//! S executes exactly 401ms, so H executes exactly 2000 - 401 = 1599ms and
//! the CPU never idles (H is always runnable).

mod common;

use cfs_sim::config::SchedulerConfig;
use cfs_sim::scenario::Scenario;
use cfs_sim::scheduler::{Event, PlaceReason};
use cfs_sim::task::{Phase, TaskSpec};
use common::{check_eq, check_in_range, log_step};

const CASE: &str = "wakeup-placement";
const BONUS_V: u64 = 2_097_152; // 2ms * 1024 * 2^20 / 1024, hand-computed

fn scenario() -> Scenario {
    Scenario {
        name: "wakeup-bounded-bonus".into(),
        description: "sleeper wakes against an always-on task".into(),
        duration_ms: 2000,
        config: SchedulerConfig::default(),
        checks: Default::default(),
        tasks: vec![
            TaskSpec {
                name: "hog".into(),
                weight: 1024,
                arrival_ms: 0,
                script: vec![Phase::Run { ms: 2000 }],
                repeats: 1,
            },
            TaskSpec {
                name: "sleeper".into(),
                weight: 1024,
                arrival_ms: 0,
                script: vec![
                    Phase::Run { ms: 1 },
                    Phase::Sleep { ms: 500 },
                    Phase::Run { ms: 400 },
                ],
                repeats: 1,
            },
        ],
    }
}

#[test]
fn wakeup_bonus_is_bounded_and_documented_in_events() {
    let (outcome, _) = common::run_scenario(&scenario());

    let wakeups: Vec<_> = outcome
        .events
        .iter()
        .filter_map(|e| match e {
            Event::Placed { tick_ms, task, vruntime, min_vruntime, reason }
                if *reason == PlaceReason::Wakeup && task == "sleeper" =>
            {
                Some((*tick_ms, *vruntime, *min_vruntime))
            }
            _ => None,
        })
        .collect();
    log_step(CASE, "wakeup-events", ">= 1", &wakeups.len().to_string(),
        "the sleeper wakes exactly once", !wakeups.is_empty());
    assert_eq!(wakeups.len(), 1, "sleeper wakes exactly once");

    let (tick, placed_v, min_v) = wakeups[0];
    // own_v after 1ms of execution is 2^20, far below min_v - bonus, so the
    // floor binds exactly.
    check_eq(CASE, "placed == min_vruntime - bonus", min_v - BONUS_V, placed_v,
        "bonus = wakeup_granularity(2ms) * NICE_0_LOAD * 2^20 / weight = 2097152");
    log_step(CASE, "wakeup-tick", "~501..506", &tick.to_string(),
        "1ms run + up to one slice of waiting + 500ms sleep",
        (500..=510).contains(&tick));
}

#[test]
fn woken_task_cannot_starve_the_incumbent() {
    let (outcome, _) = common::run_scenario(&scenario());
    let hog = &outcome.tasks[0];
    let sleeper = &outcome.tasks[1];

    check_eq(CASE, "sleeper.exec", 401, sleeper.stats.exec_ms,
        "script is 1ms + 400ms of Run phases");
    check_eq(CASE, "hog.exec", 1599, hog.stats.exec_ms,
        "busy 2000ms minus sleeper's 401ms; CPU never idles while hog is runnable");
    check_eq(CASE, "idle", 0, outcome.idle_ms, "hog is always runnable");
    check_in_range(CASE, "hog.max_wait", 0, 16, hog.stats.max_wait_ms,
        "bounded bonus (2ms) + one slice (<=4ms) + margin; unbounded placement \
         would let the sleeper monopolize the CPU for its full 400ms run");
    let finished = sleeper.stats.finish_ms.is_some();
    log_step(CASE, "sleeper.finished", "true", &finished.to_string(),
        "sleeper completes its 400ms run well before t=2000", finished);
    assert!(finished);
}
