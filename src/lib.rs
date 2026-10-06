//! Quantifier elimination and model checking for first-order formulas over
//! finite enumerated domains.
//!
//! Module map:
//! * [`syntax`] - formula/term syntax and capture-avoiding substitution.
//! * [`model`] - finite enumerated-domain models and their validation.
//! * [`eval`] - direct recursive model checking (reference implementation).
//! * [`qe`] - quantifier elimination by domain expansion, with budget.
//! * [`proof`] - run identifiers and proof/trace records.
//! * [`check`] - independent verification of elimination outcomes.
//! * [`error`] - shared error contract with four failure categories.

pub mod check;
pub mod error;
pub mod eval;
pub mod model;
pub mod proof;
pub mod qe;
pub mod syntax;

use check::CheckReport;
use error::QeError;
use model::Model;
use proof::ProofStep;
use qe::{Eliminator, QeStatus};
use serde::Serialize;
use syntax::Formula;

/// Full pipeline report: model checking, elimination, and independent
/// verification under one run id.
#[derive(Debug, Clone, Serialize)]
pub struct PipelineReport {
    pub run_id: String,
    pub model: String,
    pub status: QeStatus,
    pub unknown: bool,
    /// Truth value of the original formula from direct recursive evaluation.
    pub value: bool,
    pub result_formula: Formula,
    pub remaining_quantifiers: usize,
    pub trace: Vec<ProofStep>,
    pub check: CheckReport,
}

/// Run the whole pipeline: evaluate the original formula, eliminate
/// quantifiers under `budget`, then independently verify the outcome.
pub fn run_pipeline(
    model: &Model,
    formula: &Formula,
    budget: Option<u64>,
) -> Result<PipelineReport, QeError> {
    let value = eval::eval_closed(model, formula)?;
    let outcome = Eliminator::new(model, budget).run(formula)?;
    let check = check::verify_outcome(model, formula, &outcome)?;
    Ok(PipelineReport {
        run_id: outcome.run_id.clone(),
        model: model.name.clone(),
        status: outcome.status,
        unknown: outcome.unknown,
        value,
        result_formula: outcome.formula.clone(),
        remaining_quantifiers: outcome.remaining_quantifiers,
        trace: outcome.trace.clone(),
        check,
    })
}
