//! Fixed-formula tests: vruntime deltas, slice computation, granularity /
//! latency relationship. All reference values are hand-computed literals,
//! not produced by the code under test.
//!
//! Hand derivation (tick=1ms, NICE_0_LOAD=1024, VRUNTIME_SCALE=2^20=1048576):
//!   dv(exec_ms, w) = exec_ms * 1024 * 1048576 / w
//!   dv(1, 1024) = 1048576; dv(1, 2048) = 524288; dv(1, 512) = 2097152
//!   slice(w, sum_w) = max(2, 8*w/sum_w) with the default config
//!   period(n) = max(8, n*2)

mod common;

use cfs_sim::config::{delta_vruntime, ConfigError, SchedulerConfig, VRUNTIME_SCALE};
use cfs_sim::scenario::Scenario;
use cfs_sim::task::{Phase, TaskSpec};
use common::{check_eq, log_step};

const CASE: &str = "vruntime-math";

#[test]
fn delta_vruntime_matches_hand_computed_values() {
    check_eq(CASE, "dv(1ms,w=1024)", 1_048_576, delta_vruntime(1, 1024),
        "1ms * 1024 * 2^20 / 1024 = 2^20");
    check_eq(CASE, "dv(1ms,w=2048)", 524_288, delta_vruntime(1, 2048),
        "1ms * 1024 * 2^20 / 2048 = 2^19");
    check_eq(CASE, "dv(1ms,w=512)", 2_097_152, delta_vruntime(1, 512),
        "1ms * 1024 * 2^20 / 512 = 2^21");
    check_eq(CASE, "dv(10ms,w=1024)", 10_485_760, delta_vruntime(10, 1024),
        "10 * 2^20");
}

#[test]
fn slice_and_period_follow_latency_granularity_relation() {
    let cfg = SchedulerConfig::default();
    // ideal = 8 * 1024/4096 = 2 -> max(2, 2) = 2
    check_eq(CASE, "slice(w=1024,sum=4096)", 2, cfg.slice_ms(1024, 4096),
        "max(min_gran=2, latency*1024/4096=2)");
    // ideal = 8 * 3072/4096 = 6
    check_eq(CASE, "slice(w=3072,sum=4096)", 6, cfg.slice_ms(3072, 4096),
        "max(2, 8*3072/4096=6)");
    // ideal floors to min_granularity when latency share is smaller
    check_eq(CASE, "slice-floored(w=1024,sum=8192)", 2, cfg.slice_ms(1024, 8192),
        "max(2, 8*1024/8192=1) = 2 (min granularity floor)");
    // period: target_latency while n <= latency/min_gran = 4, else n*min_gran
    check_eq(CASE, "period(n=1)", 8, cfg.period_bound_ms(1), "max(8, 1*2)");
    check_eq(CASE, "period(n=4)", 8, cfg.period_bound_ms(4),
        "max(8, 4*2): latency still dominates at the threshold");
    check_eq(CASE, "period(n=5)", 10, cfg.period_bound_ms(5),
        "max(8, 5*2): period stretches past the threshold");
    check_eq(CASE, "period(n=8)", 16, cfg.period_bound_ms(8), "max(8, 8*2)");
}

#[test]
fn invalid_configs_are_rejected_with_categories() {
    let cases: Vec<(SchedulerConfig, ConfigError)> = vec![
        (
            SchedulerConfig { tick_ms: 0, ..Default::default() },
            ConfigError::TickZero,
        ),
        (
            SchedulerConfig { tick_ms: 4, min_granularity_ms: 2, ..Default::default() },
            ConfigError::GranularityBelowTick { min: 2, tick: 4 },
        ),
        (
            SchedulerConfig { target_latency_ms: 1, min_granularity_ms: 2, ..Default::default() },
            ConfigError::LatencyBelowGranularity { latency: 1, min: 2 },
        ),
        (
            SchedulerConfig { wakeup_granularity_ms: 16, ..Default::default() },
            ConfigError::WakeupGranularityTooLarge { wake: 16, latency: 8 },
        ),
    ];
    for (cfg, expected) in cases {
        let actual = cfg.validate().unwrap_err();
        log_step(CASE, "config-validate", &format!("{expected:?}"), &format!("{actual:?}"),
            "each invalid parameter maps to a distinct error category",
            actual == expected);
        assert_eq!(actual, expected);
    }
}

/// A lone task accumulates exactly dv per tick; blocked time never appears
/// because the task never sleeps.
#[test]
fn lone_task_vruntime_grows_linearly_with_hand_computed_slope() {
    for (weight, expected_v) in [(1024_u64, 10_485_760_u64), (2048, 5_242_880)] {
        let scenario = Scenario {
            name: format!("lone-{weight}"),
            description: String::new(),
            duration_ms: 10,
            config: SchedulerConfig::default(),
            checks: Default::default(),
            tasks: vec![TaskSpec {
                name: "solo".into(),
                weight,
                arrival_ms: 0,
                script: vec![Phase::Run { ms: 10 }],
                repeats: 1,
            }],
        };
        let (outcome, _) = common::run_scenario(&scenario);
        let task = &outcome.tasks[0];
        check_eq(CASE, &format!("vruntime(w={weight},t=10ms)"), expected_v, task.vruntime,
            "10 ticks * dv(1ms, w); vruntime advances only while executing");
        check_eq(CASE, "exec==10", 10, task.stats.exec_ms, "ran every tick");
        check_eq(CASE, "min_vruntime tracks sole task", expected_v, outcome.min_vruntime,
            "with one task min_vruntime == its vruntime");
        let pass = task.vruntime as u128 == 10 * VRUNTIME_SCALE as u128 * 1024 / weight as u128;
        log_step(CASE, "slope-identity", "v == 10*1024*2^20/w",
            &format!("v={}", task.vruntime), "fixed formula", pass);
        assert!(pass);
    }
}
