//! End-to-end request pipeline: parse-level checks, structural
//! validation, exact counting, optional independent recount, and
//! structured diagnostics for every outcome.

use crate::check::brute_force_count;
use crate::config::Config;
use crate::count::{count_models, CountOutcome};
use crate::diag::{vars_str, Category, Diagnostic};
use crate::request::Request;
use crate::syntax::Var;
use crate::validate::{validate, Rejection};
use num_bigint::BigUint;
use std::collections::BTreeSet;

/// Outcome of running one request through the pipeline.
pub struct RunResult {
    pub diagnostic: Diagnostic,
    /// Model count, present iff the request was accepted.
    pub count: Option<BigUint>,
    /// Counting proof, present iff the request was accepted.
    pub proof: Option<crate::proof::ProofRecord>,
}

impl RunResult {
    fn reject(diagnostic: Diagnostic) -> Self {
        RunResult {
            diagnostic,
            count: None,
            proof: None,
        }
    }
}

/// Run the full pipeline for one request.
pub fn run_request(req: &Request, config: &Config) -> RunResult {
    let id = req.request_id.as_str();
    let label = req.label.as_deref();

    // Stage 0: declared universe.
    let declared: BTreeSet<Var> = match &req.declared_vars {
        Some(vars) => {
            let set: BTreeSet<Var> = vars.iter().copied().collect();
            if set.len() != vars.len() {
                return RunResult::reject(
                    Diagnostic::new(
                        id,
                        Category::BadUniverse,
                        None,
                        "declared_vars contains duplicates".to_string(),
                    )
                    .with_label(label),
                );
            }
            set
        }
        None => BTreeSet::new(), // filled from root scope after syntax check
    };

    // Stage 1: syntactic well-formedness.
    let order = match req.circuit.topo_order() {
        Ok(o) => o,
        Err(e) => {
            return RunResult::reject(
                Diagnostic::new(id, Category::InvalidSyntax, None, e.to_string())
                    .with_label(label),
            );
        }
    };
    let declared = if req.declared_vars.is_some() {
        declared
    } else {
        req.circuit
            .scopes(&order)
            .into_iter()
            .find(|(n, _)| *n == req.circuit.root)
            .map(|(_, s)| s)
            .unwrap_or_default()
    };
    // The declared universe must cover every variable the circuit
    // mentions; otherwise smoothing exponents would be negative.
    let root_scope: BTreeSet<Var> = req
        .circuit
        .scopes(&order)
        .into_iter()
        .find(|(n, _)| *n == req.circuit.root)
        .map(|(_, s)| s)
        .unwrap_or_default();
    if !root_scope.is_subset(&declared) {
        let missing: Vec<Var> = root_scope.difference(&declared).copied().collect();
        return RunResult::reject(
            Diagnostic::new(
                id,
                Category::BadUniverse,
                Some(req.circuit.root),
                format!(
                    "declared universe does not cover circuit variables {}",
                    vars_str(&missing)
                ),
            )
            .with_label(label),
        );
    }

    // Stage 2: structural validation (AND decomposability first, then
    // OR determinism with evidence or bounded enumeration).
    let validation = match validate(&req.circuit, config) {
        Ok(v) => v,
        Err(rej) => {
            let diag = match rej {
                Rejection::AndNotDecomposable {
                    node,
                    var,
                    children,
                } => Diagnostic::new(
                    id,
                    Category::AndNotDecomposable,
                    Some(node),
                    format!(
                        "and node {node}: variable {var} occurs in both child {} and child {}",
                        children.0, children.1
                    ),
                ),
                Rejection::OrNotDeterministic {
                    node,
                    pair,
                    witness,
                } => {
                    let w: Vec<Var> = witness
                        .iter()
                        .filter(|(_, p)| *p)
                        .map(|(v, _)| *v)
                        .collect();
                    Diagnostic::new(
                        id,
                        Category::OrNotDeterministic,
                        Some(node),
                        format!(
                            "or node {node}: children {} and {} both satisfied by witness with true vars {}",
                            pair.0,
                            pair.1,
                            vars_str(&w)
                        ),
                    )
                }
                Rejection::Undecidable {
                    node,
                    pair,
                    scope_size,
                } => Diagnostic::new(
                    id,
                    Category::Undecidable,
                    Some(node),
                    format!(
                        "or node {node}: no determinism evidence for children {} and {}; joined scope size {scope_size} exceeds enumeration threshold {}",
                        pair.0, pair.1, config.determinism_enumeration_threshold
                    ),
                ),
            };
            return RunResult::reject(diag.with_label(label));
        }
    };

    // Stage 3: exact counting with proof record.
    let CountOutcome { total, proof } = count_models(&req.circuit, &declared, id, validation);

    // Stage 4: optional independent recount on small universes.
    if config.run_independent_check && declared.len() as u32 <= config.independent_check_threshold
    {
        let independent = brute_force_count(&req.circuit, &declared);
        if independent != total {
            return RunResult::reject(
                Diagnostic::new(
                    id,
                    Category::IndependentCheckMismatch,
                    Some(req.circuit.root),
                    format!("core count {total} != brute-force recount {independent}"),
                )
                .with_label(label),
            );
        }
    }

    RunResult {
        diagnostic: Diagnostic::new(
            id,
            Category::Accepted,
            Some(req.circuit.root),
            format!(
                "accepted: {} models over {} declared vars (top smoothing 2^{})",
                total,
                declared.len(),
                proof.top_smooth_exp
            ),
        )
        .with_label(label),
        count: Some(total),
        proof: Some(proof),
    }
}
