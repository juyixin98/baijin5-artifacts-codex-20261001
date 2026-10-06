//! Independent verification of elimination outcomes.
//!
//! The checker deliberately uses only [`crate::eval`] (the reference
//! recursive evaluator) and syntactic scans from [`crate::syntax`]; it never
//! reuses the eliminator's internals. It verifies:
//!
//! 1. trace well-formedness: sequence numbers are dense from 0 and every
//!    step carries the outcome's run id;
//! 2. status consistency: `Eliminated` implies quantifier-free and not
//!    unknown; `Partial` implies quantifiers remain and `unknown` is set;
//! 3. semantic equivalence on the model: the original formula and the
//!    outcome formula evaluate to the same truth value.
//!
//! Violations are collected in the report; callers map a failed report to
//! [`crate::error::ErrorKind::ComputationFailed`].
use crate::error::QeError;
use crate::eval::eval_closed;
use crate::model::Model;
use crate::qe::{QeOutcome, QeStatus};
use crate::syntax::Formula;
use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
pub struct CheckReport {
    pub run_id: String,
    pub ok: bool,
    pub value_original: Option<bool>,
    pub value_result: Option<bool>,
    pub violations: Vec<String>,
}

pub fn verify_outcome(
    model: &Model,
    original: &Formula,
    outcome: &QeOutcome,
) -> Result<CheckReport, QeError> {
    let mut violations = Vec::new();
    for (i, step) in outcome.trace.iter().enumerate() {
        if step.seq != i as u64 {
            violations.push(format!(
                "trace step {i} has seq {}, expected {i}",
                step.seq
            ));
        }
        if step.run_id != outcome.run_id {
            violations.push(format!(
                "trace step {i} carries run id '{}', expected '{}'",
                step.run_id, outcome.run_id
            ));
        }
    }
    let quantifier_free = outcome.formula.is_quantifier_free();
    match outcome.status {
        QeStatus::Eliminated => {
            if !quantifier_free {
                violations.push(
                    "status is 'eliminated' but the result still contains quantifiers"
                        .to_string(),
                );
            }
            if outcome.unknown {
                violations
                    .push("status is 'eliminated' but the unknown flag is set".to_string());
            }
        }
        QeStatus::Partial => {
            if quantifier_free {
                violations.push(
                    "status is 'partial' but the result is quantifier-free".to_string(),
                );
            }
            if !outcome.unknown {
                violations
                    .push("status is 'partial' but the unknown flag is not set".to_string());
            }
        }
    }
    let actual_remaining = outcome.formula.quantifier_count();
    if outcome.remaining_quantifiers != actual_remaining {
        violations.push(format!(
            "reported remaining_quantifiers={} but the result contains {actual_remaining}",
            outcome.remaining_quantifiers
        ));
    }
    let value_original = eval_closed(model, original)?;
    let value_result = eval_closed(model, &outcome.formula)?;
    if value_original != value_result {
        violations.push(format!(
            "value mismatch: original evaluates to {value_original}, result to {value_result}"
        ));
    }
    Ok(CheckReport {
        run_id: outcome.run_id.clone(),
        ok: violations.is_empty(),
        value_original: Some(value_original),
        value_result: Some(value_result),
        violations,
    })
}
