//! Construction and simplification of Craig interpolants.
//!
//! The combination rules are the classic symmetric (Pudlák) annotation rules:
//! - an A-side hypothesis carries partial interpolant `false`;
//! - a B-side hypothesis carries partial interpolant `true`;
//! - resolving on a shared pivot combines as
//!   `(x | i_neg) & (!x | i_pos)`;
//! - resolving on an A-local pivot combines as `i_pos | i_neg`;
//! - resolving on a B-local pivot combines as `i_pos & i_neg`.
//!
//! Here `i_pos` is the partial interpolant of the clause containing the
//! positive pivot literal and `i_neg` the one containing its negation.

use ipc_proof::{Proof, ProofNode, Side};
use ipc_syntax::Formula;
use std::collections::BTreeSet;

/// Intermediate formula in negation normal form used while building ITE-like
/// partial interpolants. We keep it as [`Formula`] directly.
#[derive(Debug, Clone)]
pub struct AnnotatedProof {
    pub partial: Vec<Formula>,
}

/// Compute every partial interpolant for a proof using the side information in
/// the leaf nodes. `a_vars` and `b_vars` are the *original* alphabets of both
/// sides, before Tseitin variables were introduced.
pub fn build_annotations(
    proof: &Proof,
    a_vars: &BTreeSet<String>,
    b_vars: &BTreeSet<String>,
) -> Result<AnnotatedProof, String> {
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
                    return Err(format!("invalid proof edge at node {index}"));
                }
                let i_pos = partial
                    .get(*positive)
                    .ok_or_else(|| format!("missing annotation {positive}"))?
                    .clone();
                let i_neg = partial
                    .get(*negative)
                    .ok_or_else(|| format!("missing annotation {negative}"))?
                    .clone();
                combine(pivot, &i_pos, &i_neg, a_vars, b_vars)
            }
        };
        partial.push(formula);
    }
    Ok(AnnotatedProof { partial })
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
        // (!pivot | i_neg) & (pivot | i_pos)
        // i_pos annotates the clause containing +pivot; i_neg the -pivot one.
        Formula::And(vec![
            Formula::Or(vec![Formula::not(Formula::var(pivot)), i_neg.clone()]),
            Formula::Or(vec![Formula::var(pivot), i_pos.clone()]),
        ])
    } else if in_a {
        Formula::Or(vec![i_pos.clone(), i_neg.clone()])
    } else if in_b {
        Formula::And(vec![i_pos.clone(), i_neg.clone()])
    } else {
        // Tseitin auxiliary variables are local to exactly one side; any
        // pivot seen on neither original alphabet is treated as B-local as a
        // defensive fallback, but this branch must never occur for valid
        // inputs produced by the engine.
        Formula::And(vec![i_pos.clone(), i_neg.clone()])
    }
}

/// Extract the root interpolant and aggressively simplify constants and
/// tautological/contradictory terms.
pub fn extract_interpolant(
    proof: &Proof,
    annotations: &AnnotatedProof,
) -> Result<Formula, String> {
    annotations
        .partial
        .get(proof.root)
        .cloned()
        .map(simplify)
        .ok_or_else(|| "proof root has no partial interpolant".to_string())
}

/// Recursively simplify constants, duplicate terms and direct complements.
pub fn simplify(formula: Formula) -> Formula {
    match formula {
        constant @ (Formula::Const(_) | Formula::Var(_)) => constant,
        Formula::Not(inner) => match simplify(*inner) {
            Formula::Const(v) => Formula::Const(!v),
            Formula::Not(inner_inner) => *inner_inner,
            other => Formula::not(other),
        },
        Formula::And(items) => {
            let mut simplified: Vec<Formula> = Vec::new();
            for item in items {
                match simplify(item) {
                    Formula::Const(true) => {}
                    Formula::Const(false) => return Formula::Const(false),
                    other => simplified.push(other),
                }
            }
            if simplified.is_empty() {
                return Formula::Const(true);
            }
            merge_complementary(&mut simplified, true);
            if simplified.len() == 1 {
                simplified.pop().unwrap()
            } else {
                Formula::And(simplified)
            }
        }
        Formula::Or(items) => {
            let mut simplified: Vec<Formula> = Vec::new();
            for item in items {
                match simplify(item) {
                    Formula::Const(false) => {}
                    Formula::Const(true) => return Formula::Const(true),
                    other => simplified.push(other),
                }
            }
            if simplified.is_empty() {
                return Formula::Const(false);
            }
            merge_complementary(&mut simplified, false);
            if simplified.len() == 1 {
                simplified.pop().unwrap()
            } else {
                Formula::Or(simplified)
            }
        }
        Formula::Imp(a, b) => simplify(Formula::Or(vec![
            Formula::not(*a),
            *b,
        ])),
    }
}

fn merge_complementary(items: &mut Vec<Formula>, conjunction: bool) {
    let mut unique: Vec<Formula> = Vec::new();
    for item in items.drain(..) {
        if !unique.contains(&item) {
            unique.push(item);
        }
    }
    // x & !x -> false ; x | !x -> true
    let mut complement_index = None;
    'outer: for (i, item) in unique.iter().enumerate() {
        if let Formula::Var(name) = item {
            for (j, other) in unique.iter().enumerate() {
                if let Formula::Not(inner) = other {
                    if let Formula::Var(other_name) = inner.as_ref() {
                        if other_name == name {
                            complement_index = Some((i, j));
                            break 'outer;
                        }
                    }
                }
            }
        }
    }
    if let Some((i, j)) = complement_index {
        let first = i.min(j);
        let second = i.max(j);
        let mut replacement = vec![Formula::Const(!conjunction)];
        let _ = (first, second);
        unique.clear();
        unique.append(&mut replacement);
    }
    *items = unique;
}

/// Assert at construction time that a formula uses only common variables.
pub fn assert_common_alphabet(
    interpolant: &Formula,
    common: &BTreeSet<String>,
) -> Result<(), Vec<String>> {
    let used: BTreeSet<String> = interpolant.variables().into_iter().collect();
    let offenders: Vec<String> = used.difference(common).cloned().collect();
    if offenders.is_empty() {
        Ok(())
    } else {
        Err(offenders)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ipc_proof::{Clause, Literal, ProofBuilder, Side};

    fn common_set(names: &[&str]) -> BTreeSet<String> {
        names.iter().map(|name| name.to_string()).collect()
    }

    #[test]
    fn interpolant_for_p_and_not_p_is_p() {
        let mut builder = ProofBuilder::new();
        let pos = builder.add_hypothesis(
            Side::A,
            Clause::unit(Literal::positive("p")),
            "A".to_string(),
        );
        let neg = builder.add_hypothesis(
            Side::B,
            Clause::unit(Literal::negative("p")),
            "B".to_string(),
        );
        let root = builder.add_resolution("p".to_string(), pos, neg, Clause::empty());
        let proof = builder.finish(root);
        let annotations = build_annotations(
            &proof,
            &common_set(&["p"]),
            &common_set(&["p"]),
        )
        .unwrap();
        let interpolant = extract_interpolant(&proof, &annotations).unwrap();
        assert_eq!(interpolant, Formula::var("p"));
    }

    #[test]
    fn simplifies_ite_for_shared_chain_to_d() {
        // A: a & (!a | d)  B: !d, common {d} => interpolant d.
        let mut builder = ProofBuilder::new();
        let a1 = builder.add_hypothesis(
            Side::A,
            Clause::unit(Literal::positive("a")),
            "A".to_string(),
        );
        let a2 = builder.add_hypothesis(
            Side::A,
            Clause::new(vec![Literal::negative("a"), Literal::positive("d")]),
            "A".to_string(),
        );
        let b1 = builder.add_hypothesis(
            Side::B,
            Clause::unit(Literal::negative("d")),
            "B".to_string(),
        );
        let d_unit = builder.add_resolution(
            "a".to_string(),
            a1,
            a2,
            Clause::unit(Literal::positive("d")),
        );
        let root = builder.add_resolution("d".to_string(), d_unit, b1, Clause::empty());
        let proof = builder.finish(root);
        let annotations = build_annotations(
            &proof,
            &common_set(&["a", "d"]),
            &common_set(&["d"]),
        )
        .unwrap();
        let interpolant = extract_interpolant(&proof, &annotations).unwrap();
        assert_eq!(interpolant, Formula::var("d"));
    }
}
