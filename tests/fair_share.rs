//! Long-term share tests with hand-computed references.
//!
//! fair-weights-1-3: weights 1024:3072 -> shares 1/4 and 3/4 of 6000ms busy
//! time -> exec 1500ms / 4500ms (slices 2ms/6ms interleave exactly 2:6 per
//! 8ms latency period).
//! equal-trio: three weight-1024 tasks -> 1000ms each of 3000ms.

mod common;

use cfs_sim::scenario::find_builtin;
use common::{check_close, check_eq, check_in_range, log_step};

#[test]
fn fair_weights_1_3_long_term_shares() {
    let case = "fair-share-1-3";
    let scenario = find_builtin("fair-weights-1-3").unwrap();
    let (outcome, report) = common::run_scenario(&scenario);
    let light = &outcome.tasks[0];
    let heavy = &outcome.tasks[1];

    check_eq(case, "elapsed", 6000, outcome.elapsed_ms, "duration bound reached");
    check_eq(case, "idle", 0, outcome.idle_ms,
        "both tasks always runnable -> CPU never idles");
    check_in_range(case, "light.exec", 1400, 1600, light.stats.exec_ms,
        "share 1/4 of 6000ms = 1500ms; slice quantization allows a small band");
    check_in_range(case, "heavy.exec", 4400, 4600, heavy.stats.exec_ms,
        "share 3/4 of 6000ms = 4500ms");

    let busy = outcome.busy_ms as f64;
    check_close(case, "light.share", 0.25, 0.02, light.stats.exec_ms as f64 / busy,
        "w_light/(w_light+w_heavy) = 1024/4096 = 0.25");
    check_close(case, "heavy.share", 0.75, 0.02, heavy.stats.exec_ms as f64 / busy,
        "3072/4096 = 0.75");

    // The independent metrics layer must agree.
    for check in &report.checks {
        log_step(case, &format!("report-check:{}", check.name), "pass",
            &format!("pass={} {}", check.passed, check.detail), &check.basis, check.passed);
        assert!(check.passed, "{}", check.name);
    }
    assert!(report.passed);
}

#[test]
fn equal_trio_shares_and_exec() {
    let case = "equal-trio";
    let scenario = find_builtin("equal-trio").unwrap();
    let (outcome, report) = common::run_scenario(&scenario);
    let busy = outcome.busy_ms as f64;
    for task in &outcome.tasks {
        check_in_range(case, &format!("{}.exec", task.spec.name), 950, 1050,
            task.stats.exec_ms, "1/3 of 3000ms = 1000ms per task");
        check_close(case, &format!("{}.share", task.spec.name), 1.0 / 3.0, 0.02,
            task.stats.exec_ms as f64 / busy, "equal weights -> equal shares");
    }
    assert!(report.passed, "all report checks must pass: {report:?}");
}

/// The engine is deterministic: identical inputs -> identical execution
/// totals. Guards against accidental nondeterminism in queue ordering.
#[test]
fn determinism_same_inputs_same_totals() {
    let case = "determinism";
    let scenario = find_builtin("fair-weights-1-3").unwrap();
    let (first, _) = common::run_scenario(&scenario);
    let (second, _) = common::run_scenario(&scenario);
    for (a, b) in first.tasks.iter().zip(second.tasks.iter()) {
        check_eq(case, &format!("{}:exec-repeatable", a.spec.name),
            a.stats.exec_ms, b.stats.exec_ms,
            "BTreeMap ordering + insertion seq make scheduling deterministic");
    }
}
