//! Reasoning core for STLC proof terms: type checking and strong
//! normalization plus invariant verification (type preservation, free
//! variable tracking, alpha equivalence).

pub mod error;
pub mod normalize;
pub mod subst;
pub mod typecheck;

use serde::{Deserialize, Serialize};
use stlc_proof::{Binding, TypingProof};
use stlc_syntax::db::{from_locally_nameless, to_locally_nameless, DbTerm};
use stlc_syntax::pretty::{db_to_string, term_to_string};
use stlc_syntax::{free_vars, Term, Type};

pub use error::{DriverError, NormError, TypeError};
pub use normalize::{Normalizer, Step, StepKind};
pub use typecheck::check_term;

/// Full request in surface syntax.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CheckRequest {
    pub term: Term,
    #[serde(default)]
    pub free_signature: Vec<Binding>,
    #[serde(default = "default_budget")]
    pub budget: usize,
}

fn default_budget() -> usize {
    1024
}

/// One verified fact about a normalization run.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct InvariantReport {
    pub kind: String,
    pub passed: bool,
    pub detail: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CheckResponse {
    pub before_type: Type,
    pub after_type: Type,
    pub initial_term: Term,
    pub normal_form: Term,
    pub normal_form_db: String,
    pub free_vars_before: Vec<String>,
    pub free_vars_after: Vec<String>,
    pub steps: Vec<Step>,
    pub steps_used: usize,
    pub budget: usize,
    pub proof: TypingProof,
    pub invariants: Vec<InvariantReport>,
}

/// Alpha equivalence of locally nameless terms.
///
/// Representation already identifies terms modulo renaming; the extra
/// well-formedness guard rejects terms with dangling bound indices.
pub fn alpha_equivalent(left: &DbTerm, right: &DbTerm) -> bool {
    left.is_locally_closed() && right.is_locally_closed() && left == right
}

/// Run the full pipeline: type check, normalize, and independently verify the
/// preservation/free-variable invariants on the result.
pub fn run(request: CheckRequest) -> Result<CheckResponse, DriverError> {
    let CheckRequest {
        term,
        free_signature,
        budget,
    } = request;

    let initial = to_locally_nameless(&term);
    let fv_surface = free_vars_surface(&term);
    let proof = check_term(&initial, free_signature.clone())
        .map_err(|e| DriverError::Check(NormError::Type(e)))?;
    let before_type = proof.root.ty.clone();

    let mut normalizer = Normalizer::new(budget);
    let normal_form = normalizer
        .normalize(&initial)
        .map_err(DriverError::Check)?;

    // Type preservation: normal form must re-check at the same type.
    let preservation_proof = check_term(&normal_form, free_signature.clone())
        .map_err(|e| DriverError::Check(NormError::Internal {
            detail: format!("normal form failed to re-typecheck: {e}"),
        }))?;
    let after_type = preservation_proof.root.ty.clone();

    let fv_before = initial.free_vars();
    let fv_after = normal_form.free_vars();

    let mut invariants = Vec::new();
    invariants.push(InvariantReport {
        kind: "type_preservation".to_string(),
        passed: before_type == after_type,
        detail: format!("before {before_type:?}, after {after_type:?}"),
    });
    invariants.push(InvariantReport {
        kind: "free_vars_subset".to_string(),
        passed: fv_after.iter().all(|v| fv_before.contains(v)),
        detail: format!("before {fv_before:?}, after {fv_after:?}"),
    });
    invariants.push(InvariantReport {
        kind: "free_vars_match".to_string(),
        passed: fv_before == fv_after,
        detail: format!("before {fv_before:?}, after {fv_after:?}"),
    });
    invariants.push(InvariantReport {
        kind: "locally_closed".to_string(),
        passed: normal_form.is_locally_closed(),
        detail: "no dangling bound indices in the normal form".to_string(),
    });
    invariants.push(InvariantReport {
        kind: "declared_free_vars_cover_term".to_string(),
        passed: fv_before
            .iter()
            .all(|v| free_signature.iter().any(|b| &b.name == v)),
        detail: "every term free variable has a declared type".to_string(),
    });

    if invariants.iter().any(|r| !r.passed) {
        return Err(DriverError::Check(NormError::Internal {
            detail: format!("post-normalization invariant failed: {invariants:?}"),
        }));
    }

    Ok(CheckResponse {
        normal_form: from_locally_nameless(&normal_form),
        normal_form_db: db_to_string(&normal_form),
        initial_term: term,
        before_type,
        after_type,
        free_vars_before: fv_surface,
        free_vars_after: fv_after,
        steps: normalizer.steps,
        steps_used: normalizer.used,
        budget,
        proof,
        invariants,
    })
}

fn free_vars_surface(term: &Term) -> Vec<String> {
    let mut v = free_vars(term);
    v.sort();
    v
}

/// Convenience: parse a term string and run the pipeline.
pub fn run_source(
    source: &str,
    free_signature: Vec<Binding>,
    budget: usize,
) -> Result<CheckResponse, DriverError> {
    let term = stlc_syntax::parse::parse_term(source).map_err(|e| DriverError::Parse {
        message: e.message,
        offset: e.offset,
    })?;
    run(CheckRequest {
        term,
        free_signature,
        budget,
    })
}

#[cfg(test)]
fn _assert_type_used(_: &Type) {}

/// Re-check an existing proof's free-variable coverage against a signature.
pub fn declared_free_vars(proof: &TypingProof) -> Vec<String> {
    proof
        .free_signature
        .iter()
        .map(|b| b.name.clone())
        .collect()
}

/// Surface rendering of a term (used in logs).
pub fn render(term: &Term) -> String {
    term_to_string(term)
}
