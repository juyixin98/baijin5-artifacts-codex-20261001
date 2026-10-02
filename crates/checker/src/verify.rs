//! Independent acceptance procedure for a claimed interpolant.
//!
//! The checker implements its own semantics:
//! - the common-alphabet condition from variable sets;
//! - the two entailment conditions by exhaustive truth tables;
//! - the refutation record by replaying every resolution step and by
//!   recomputing partial interpolants with the symmetric annotation rules.
//!
//! It never calls into `ipc-core`, so an engine bug cannot validate itself.

use crate::truth_table::{check_implication, is_satisfiable, sorted_universe};
use ipc_proof::{Proof, ProofNode, Side};
use ipc_syntax::Formula;
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum VerificationFailure {
    /// Candidate interpolant mentions a variable that is not common to A and B.
    CommonVariableViolation { offenders: Vec<String> },
    /// The antecedent does not imply the interpolant, with witness.
    AntecedentImplicationFailure { witness: BTreeMap<String, bool> },
    /// The interpolant does not imply the negation of the consequent.
    ConsequentExclusionFailure { witness: BTreeMap<String, bool> },
    /// `A & B` is jointly satisfiable, so no interpolant should exist.
    JointlySatisfiable { witness: BTreeMap<String, bool> },
    /// Structural replay of the resolution proof failed.
    ProofReplayFailure { message: String },
    /// Partial interpolants computed by the checker do not match the claim.
    AnnotationMismatch { node: usize },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CheckedClause {
    pub side: Side,
    pub literals: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct VerificationReport {
    pub a_variables: Vec<String>,
    pub b_variables: Vec<String>,
    pub common_variables: Vec<String>,
    pub failures: Vec<VerificationFailure>,
}

impl VerificationReport {
    pub fn accepted(&self) -> bool {
        self.failures.is_empty()
    }
}

fn variable_set(formula: &Formula) -> BTreeSet<String> {
    formula.variables().into_iter().collect()
}

/// Semantic-only verification of the three Craig conditions plus the required
/// existence precondition (`A & B` unsatisfiable).
pub fn verify_semantic(a: &Formula, b: &Formula, interpolant: &Formula) -> VerificationReport {
    let a_vars = variable_set(a);
    let b_vars = variable_set(b);
    let common: BTreeSet<String> = a_vars.intersection(&b_vars).cloned().collect();

    let mut failures = Vec::new();

    let used: BTreeSet<String> = interpolant.variables().into_iter().collect();
    let mut offenders: Vec<String> = used.difference(&common).cloned().collect();
    offenders.sort();
    if !offenders.is_empty() {
        failures.push(VerificationFailure::CommonVariableViolation { offenders });
    }

    if let Err(witness) = check_implication(a, interpolant) {
        failures.push(VerificationFailure::AntecedentImplicationFailure { witness });
    }

    let not_b = Formula::not(b.clone());
    if let Err(witness) = check_implication(interpolant, &not_b) {
        failures.push(VerificationFailure::ConsequentExclusionFailure { witness });
    }

    let conjunction = Formula::And(vec![a.clone(), b.clone()]);
    if let Some(witness) = is_satisfiable(&conjunction) {
        failures.push(VerificationFailure::JointlySatisfiable { witness });
    }

    VerificationReport {
        a_variables: a_vars.into_iter().collect(),
        b_variables: b_vars.into_iter().collect(),
        common_variables: common.into_iter().collect(),
        failures,
    }
}

/// Fully verify a claim: semantics plus independently replaying the proof and
/// recomputing every partial interpolant.
pub fn verify_full(
    a: &Formula,
    b: &Formula,
    interpolant: &Formula,
    proof: &Proof,
) -> VerificationReport {
    let mut report = verify_semantic(a, b, interpolant);
    let a_vars = report.a_variables.iter().cloned().collect();
    let b_vars = report.b_variables.iter().cloned().collect();
    if let Err(failure) = replay_proof(proof, &a_vars, &b_vars, interpolant) {
        report.failures.push(failure);
    }
    report
}

/// Independently replay the proof and recompute the root partial interpolant.
pub fn replay_proof(
    proof: &Proof,
    a_vars: &BTreeSet<String>,
    b_vars: &BTreeSet<String>,
    expected_root: &Formula,
) -> Result<(), VerificationFailure> {
    proof
        .validate()
        .map_err(|error| VerificationFailure::ProofReplayFailure {
            message: format!("{error:?}"),
        })?;

    let mut partial: Vec<Formula> = Vec::with_capacity(proof.nodes.len());
    for (index, node) in proof.nodes.iter().enumerate() {
        let formula = match node {
            ProofNode::Hypothesis { side, .. } => match side {
                Side::A => Formula::Const(false),
                Side::B => Formula::Const(true),
            },
            ProofNode::Resolve {
                pivot,
                positive,
                negative,
                ..
            } => {
                if *positive >= index || *negative >= index {
                    return Err(VerificationFailure::ProofReplayFailure {
                        message: format!("non-acyclic edge at node {index}"),
                    });
                }
                combine(
                    pivot,
                    &partial[*positive],
                    &partial[*negative],
                    a_vars,
                    b_vars,
                )
            }
        };
        partial.push(formula);
    }

    let recomputed = simplify(partial[proof.root].clone());
    let expected = simplify(expected_root.clone());
    if recomputed != expected {
        return Err(VerificationFailure::AnnotationMismatch { node: proof.root });
    }
    Ok(())
}

fn combine(
    pivot: &str,
    i_pos: &Formula,
    i_neg: &Formula,
    a_vars: &BTreeSet<String>,
    b_vars: &BTreeSet<String>,
) -> Formula {
    let in_a = a_vars.contains(pivot);
    let in_b = b_vars.contains(pivot);
    if in_a && in_b {
        Formula::And(vec![
            Formula::Or(vec![Formula::not(Formula::var(pivot)), i_neg.clone()]),
            Formula::Or(vec![Formula::var(pivot), i_pos.clone()]),
        ])
    } else if in_a {
        Formula::Or(vec![i_pos.clone(), i_neg.clone()])
    } else {
        // B-local variables and side-local Tseitin auxiliaries combine with &.
        Formula::And(vec![i_pos.clone(), i_neg.clone()])
    }
}

fn simplify(formula: Formula) -> Formula {
    match formula {
        constant @ (Formula::Const(_) | Formula::Var(_)) => constant,
        Formula::Not(inner) => match simplify(*inner) {
            Formula::Const(v) => Formula::Const(!v),
            Formula::Not(inner_inner) => *inner_inner,
            other => Formula::not(other),
        },
        Formula::And(items) => {
            let mut kept: Vec<Formula> = Vec::new();
            for item in items {
                match simplify(item) {
                    Formula::Const(true) => {}
                    Formula::Const(false) => return Formula::Const(false),
                    other => kept.push(other),
                }
            }
            if kept.is_empty() {
                Formula::Const(true)
            } else if kept.len() == 1 {
                kept.pop().unwrap()
            } else {
                Formula::And(kept)
            }
        }
        Formula::Or(items) => {
            let mut kept: Vec<Formula> = Vec::new();
            for item in items {
                match simplify(item) {
                    Formula::Const(false) => {}
                    Formula::Const(true) => return Formula::Const(true),
                    other => kept.push(other),
                }
            }
            if kept.is_empty() {
                Formula::Const(false)
            } else if kept.len() == 1 {
                kept.pop().unwrap()
            } else {
                Formula::Or(kept)
            }
        }
        Formula::Imp(a, b) => simplify(Formula::Or(vec![Formula::not(*a), *b])),
    }
}

/// Used by tests to build the universe over a pair explicitly.
pub fn pair_universe(a: &Formula, b: &Formula) -> Vec<String> {
    sorted_universe(&[a, b])
}
