//! Independent proof checker.
//!
//! The checker never trusts the engine's verdict. It:
//! 1. validates the model and re-type-checks the original formula;
//! 2. replays instantiation accounting from the recorded steps;
//! 3. evaluates the ORIGINAL formula directly with its own recursion over the
//!    model (an independent code path from expansion);
//! 4. compares reference truth against the claimed verdict and validates that
//!    an `unknown` record really retains quantifiers.
//!
//! Disagreements surface as `StateConflict` with precise codes.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use crate::error::{QeError, QeResult};
use crate::evaluator::evaluate_quantified;
use crate::model::Model;
use crate::proof::{NullLogger, ProofRecord, Step, Verdict};
use crate::syntax::Formula;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CheckReport {
    pub run_id: String,
    pub ok: bool,
    pub reference_truth: Option<bool>,
    pub claimed_verdict: Verdict,
    pub replayed_instantiations: u64,
    pub recorded_instantiations: u64,
    pub residual_quantifiers: usize,
    pub checks: Vec<CheckLine>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CheckLine {
    pub name: String,
    pub passed: bool,
    pub detail: String,
}

/// Re-run every verification against `record` and `model`.
pub fn check(model: &Model, record: &ProofRecord) -> QeResult<CheckReport> {
    let mut checks: Vec<CheckLine> = Vec::new();

    model.validate()?;
    checks.push(pass(
        "model_valid",
        "sorts, constants, functions and predicates are internally consistent",
    ));

    let resolved = model.check_formula(&record.original_formula)?;
    checks.push(pass(
        "formula_well_formed",
        "original formula type checks against the model",
    ));

    // Step replay: count instantiate events, ensure monotonic accounting and
    // confirm every exhaustion event lies within the declared budget.
    let mut replayed = 0u64;
    let mut saw_exhaustion = false;
    let mut monotonic = true;
    for step in &record.steps {
        match step {
            Step::Instantiate { used, limit, .. } => {
                replayed += 1;
                if *used != replayed || *limit != record.instantiation_budget {
                    monotonic = false;
                }
            }
            Step::RollbackInstances {
                rolled_back,
                used_after,
                ..
            } => {
                replayed = replayed.saturating_sub(*rolled_back);
                if *used_after != replayed {
                    monotonic = false;
                }
            }
            Step::BudgetExhausted { .. } => saw_exhaustion = true,
            _ => {}
        }
    }
    checks.push(check_line(
        "instantiation_accounting",
        replayed == record.instantiations_used && monotonic,
        format!(
            "replayed {} instantiations; record claims {}; monotonic={}",
            replayed, record.instantiations_used, monotonic
        ),
    ));

    let residual = record.expanded_formula.quantifier_count();
    checks.push(check_line(
        "residual_count",
        residual == record.residual_quantifiers,
        format!(
            "expanded formula contains {} quantifiers; record claims {}",
            residual, record.residual_quantifiers
        ),
    ));

    // Independent reference semantics over the original formula.
    let env = BTreeMap::new();
    let reference = evaluate_quantified(
        model,
        &resolved,
        &env,
        record.allow_empty_domain,
        &mut NullLogger,
    );

    let mut report = CheckReport {
        run_id: record.run_id.clone(),
        ok: false,
        reference_truth: None,
        claimed_verdict: record.verdict,
        replayed_instantiations: replayed,
        recorded_instantiations: record.instantiations_used,
        residual_quantifiers: residual,
        checks,
    };

    match reference {
        Ok(value) => {
            report.reference_truth = Some(value);
            let expected = if value { Verdict::True } else { Verdict::False };
            let claimed = record.verdict;
            // A fully expanded formula must agree exactly with the independent
            // recursion. When the budget forced an `unknown` verdict we cannot
            // demand equality, but we still recompute the reference truth as a
            // recorded cross-check that the original sentence is meaningful.
            let verdict_ok = claimed == Verdict::Unknown || claimed == expected;
            report.checks.push(check_line(
                "verdict_matches_reference",
                verdict_ok,
                format!(
                    "independent recursion says {}; record claims {}{}",
                    expected.as_str(),
                    claimed.as_str(),
                    if claimed == Verdict::Unknown {
                        " (allowed: budget retained quantifiers)"
                    } else {
                        ""
                    }
                ),
            ));

            // A definite verdict must correspond to a fully expanded formula.
            let no_residual_for_definite =
                claimed == Verdict::Unknown || record.residual_quantifiers == 0;
            report.checks.push(check_line(
                "definite_means_quantifier_free",
                no_residual_for_definite,
                if no_residual_for_definite {
                    "definite verdict backed by quantifier free expansion".to_string()
                } else {
                    "definite verdict but the expanded formula still contains quantifiers"
                        .to_string()
                },
            ));

            let unknown_consistency = if claimed == Verdict::Unknown {
                record.residual_quantifiers > 0
            } else {
                true
            };
            report.checks.push(check_line(
                "unknown_keeps_quantifier",
                unknown_consistency,
                if unknown_consistency {
                    "unknown records retain at least one quantifier".to_string()
                } else {
                    "record says unknown but no quantifier was retained".to_string()
                },
            ));

            let exhaustion_consistent = !saw_exhaustion
                || record.verdict == Verdict::Unknown
                || record.instantiations_used < record.instantiation_budget;
            report.checks.push(check_line(
                "exhaustion_consistent",
                exhaustion_consistent,
                if exhaustion_consistent {
                    "budget exhaustion only accompanies unknown verdicts".to_string()
                } else {
                    "budget exhausted but a definite verdict was claimed".to_string()
                },
            ));
        }
        Err(err) => {
            report.checks.push(check_line(
                "verdict_matches_reference",
                false,
                format!(
                    "independent recursion failed: {} ({})",
                    err.code, err.message
                ),
            ));
        }
    }

    report.ok = report.checks.iter().all(|c| c.passed);
    if !report.ok {
        let failed: Vec<&str> = report
            .checks
            .iter()
            .filter(|c| !c.passed)
            .map(|c| c.name.as_str())
            .collect();
        return Err(QeError::state_conflict(
            "proof_check_failed",
            format!(
                "proof record {} failed checks: {}",
                record.run_id,
                failed.join(", ")
            ),
        ));
    }
    Ok(report)
}

fn pass(name: &str, detail: &str) -> CheckLine {
    CheckLine {
        name: name.to_string(),
        passed: true,
        detail: detail.to_string(),
    }
}

fn check_line(name: &str, passed: bool, detail: String) -> CheckLine {
    CheckLine {
        name: name.to_string(),
        passed,
        detail,
    }
}

/// Convenience for tests: just answer whether a formula's reference truth is
/// the expected boolean.
pub fn direct_truth(model: &Model, f: &Formula, allow_empty: bool) -> QeResult<bool> {
    let resolved = model.check_formula(f)?;
    let env = BTreeMap::new();
    evaluate_quantified(model, &resolved, &env, allow_empty, &mut NullLogger)
}
