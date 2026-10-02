//! Failure categories: invalid inputs and abnormal terminations must surface
//! as distinct, typed errors — never as success.

mod common;

use cfs_sim::config::SchedulerConfig;
use cfs_sim::scenario::{Scenario, ScenarioError};
use cfs_sim::scheduler::{Engine, RunFailure};
use cfs_sim::task::{Phase, SpecError, TaskSpec};
use common::log_step;

const CASE: &str = "failure-modes";

fn spec(name: &str, weight: u64, script: Vec<Phase>) -> TaskSpec {
    TaskSpec {
        name: name.into(),
        weight,
        arrival_ms: 0,
        script,
        repeats: 1,
    }
}

#[test]
fn invalid_task_specs_are_rejected_with_categories() {
    let cases: Vec<(TaskSpec, SpecError)> = vec![
        (
            spec("w0", 0, vec![Phase::Run { ms: 1 }]),
            SpecError::ZeroWeight { task: "w0".into() },
        ),
        (
            spec("empty", 1024, vec![]),
            SpecError::EmptyScript { task: "empty".into() },
        ),
        (
            spec("zrun", 1024, vec![Phase::Run { ms: 0 }]),
            SpecError::ZeroPhase { task: "zrun".into() },
        ),
        (
            spec("zsleep", 1024, vec![Phase::Sleep { ms: 0 }]),
            SpecError::ZeroPhase { task: "zsleep".into() },
        ),
    ];
    for (task_spec, expected) in cases {
        let actual = Engine::new(SchedulerConfig::default(), vec![task_spec], 10)
            .err()
            .expect("invalid spec must be rejected");
        log_step(CASE, "spec-validation", &format!("{expected:?}"),
            &format!("{actual:?}"),
            "each invalid input maps to a distinct error variant",
            actual == expected);
        assert_eq!(actual, expected);
    }
}

#[test]
fn runaway_run_hits_tick_budget_and_is_reported_as_failure() {
    let scenario = Scenario {
        name: "never-ending".into(),
        description: "duration far beyond the tick budget".into(),
        duration_ms: 1_000_000_000,
        config: SchedulerConfig::default(),
        checks: Default::default(),
        tasks: vec![spec("spin", 1024, vec![Phase::Run { ms: 1_000_000_000 }])],
    };
    let engine = Engine::new(scenario.config, scenario.tasks.clone(), scenario.duration_ms)
        .unwrap()
        .with_max_ticks(1_000);
    let actual = engine.run().unwrap_err();
    let expected = RunFailure::ExceededMaxTicks { max_ticks: 1_000 };
    log_step(CASE, "tick-budget", &format!("{expected:?}"), &format!("{actual:?}"),
        "a run that cannot finish must fail with ExceededMaxTicks, not succeed",
        actual == expected);
    assert_eq!(actual, expected);
}

#[test]
fn scenario_loading_errors_are_categorized() {
    let parse_err = Scenario::from_json("{not json").unwrap_err();
    let is_parse = matches!(parse_err, ScenarioError::Parse(_));
    log_step(CASE, "json-parse", "Parse", &format!("{parse_err:?}"),
        "malformed JSON is a parse error", is_parse);
    assert!(is_parse);

    let unknown = cfs_sim::scenario::find_builtin("no-such-scenario").unwrap_err();
    let is_unknown = matches!(unknown, ScenarioError::Unknown(_));
    log_step(CASE, "unknown-builtin", "Unknown", &format!("{unknown:?}"),
        "unknown builtin name is its own category", is_unknown);
    assert!(is_unknown);

    let invalid = Scenario::from_json(
        r#"{"name":"bad","duration_ms":10,"tasks":[{"name":"t","weight":0,"script":[{"type":"run","ms":1}],"repeats":1}]}"#,
    )
    .unwrap_err();
    let is_spec = matches!(invalid, ScenarioError::Spec(SpecError::ZeroWeight { .. }));
    log_step(CASE, "json-spec-validation", "Spec(ZeroWeight)", &format!("{invalid:?}"),
        "validation runs on loaded fixtures too", is_spec);
    assert!(is_spec);
}
