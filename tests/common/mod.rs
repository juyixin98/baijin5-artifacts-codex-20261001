//! Shared helpers for integration tests: run a scenario through the real
//! engine and emit structured, greppable log lines that tie every assertion
//! to its inputs, the expected value, the observed value and the judgment
//! basis. Logs go to stderr; use `cargo test -- --nocapture` to see them.

#![allow(dead_code)]

use cfs_sim::metrics::{build_report, RunReport};
use cfs_sim::scenario::Scenario;
use cfs_sim::scheduler::{Engine, RunOutcome};

pub const TEST_RUN_ID: &str = "test-run";

pub fn run_scenario(scenario: &Scenario) -> (RunOutcome, RunReport) {
    let engine = Engine::new(
        scenario.config,
        scenario.tasks.clone(),
        scenario.duration_ms,
    )
    .expect("scenario specs must be valid");
    let outcome = engine.run().expect("run must complete within tick budget");
    let report = build_report(TEST_RUN_ID, scenario, &outcome, None);
    (outcome, report)
}

/// Emit one structured judgment line. Always call this BEFORE the assert so
/// the evidence is visible even when the test fails.
pub fn log_step(case: &str, step: &str, expected: &str, actual: &str, basis: &str, pass: bool) {
    eprintln!(
        "[CFS-SIM-TEST] version={} run={} case={} step={} expected={} actual={} basis={} verdict={}",
        cfs_sim::VERSION,
        TEST_RUN_ID,
        case,
        step,
        expected,
        actual,
        basis,
        if pass { "PASS" } else { "FAIL" }
    );
}

/// Log + assert an exact u64 equality.
pub fn check_eq(case: &str, step: &str, expected: u64, actual: u64, basis: &str) {
    log_step(
        case,
        step,
        &expected.to_string(),
        &actual.to_string(),
        basis,
        expected == actual,
    );
    assert_eq!(expected, actual, "case={case} step={step}");
}

/// Log + assert a value lies inside an inclusive range.
pub fn check_in_range(case: &str, step: &str, lo: u64, hi: u64, actual: u64, basis: &str) {
    let pass = (lo..=hi).contains(&actual);
    log_step(
        case,
        step,
        &format!("[{lo}, {hi}]"),
        &actual.to_string(),
        basis,
        pass,
    );
    assert!(
        (lo..=hi).contains(&actual),
        "case={case} step={step}: {actual} not in [{lo}, {hi}]"
    );
}

/// Log + assert a float is within tolerance of a hand-computed reference.
pub fn check_close(case: &str, step: &str, expected: f64, tol: f64, actual: f64, basis: &str) {
    let pass = (actual - expected).abs() <= tol;
    log_step(
        case,
        step,
        &format!("{expected:.4} +/- {tol}"),
        &format!("{actual:.6}"),
        basis,
        pass,
    );
    assert!(
        pass,
        "case={case} step={step}: |{actual} - {expected}| > {tol}"
    );
}
