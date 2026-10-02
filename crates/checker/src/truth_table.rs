//! Independent truth-table semantics.
//!
//! Nothing in this module calls into the proving engine: formulas are
//! evaluated directly over enumerated assignments, so the checker can detect
//! a buggy interpolant producer instead of trusting it.

use ipc_syntax::{evaluate, Formula};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Validity {
    Valid,
    Invalid(BTreeMap<String, bool>),
}

/// Enumerate every assignment over `variables` and test `predicate`.
/// Returns the first falsifying assignment, if any.
pub fn find_counterexample(
    variables: &[String],
    predicate: impl Fn(&BTreeMap<String, bool>) -> bool,
) -> Option<BTreeMap<String, bool>> {
    let mut assignment = BTreeMap::new();
    search(variables, 0, &mut assignment, &predicate)
}

fn search(
    variables: &[String],
    index: usize,
    assignment: &mut BTreeMap<String, bool>,
    predicate: &impl Fn(&BTreeMap<String, bool>) -> bool,
) -> Option<BTreeMap<String, bool>> {
    if index == variables.len() {
        return if predicate(assignment) {
            None
        } else {
            Some(assignment.clone())
        };
    }
    for value in [false, true] {
        assignment.insert(variables[index].clone(), value);
        if let Some(counterexample) = search(variables, index + 1, assignment, predicate) {
            return Some(counterexample);
        }
    }
    assignment.remove(&variables[index]);
    None
}

pub fn sorted_universe(formulas: &[&Formula]) -> Vec<String> {
    let mut vars = BTreeSet::new();
    for formula in formulas {
        for variable in formula.variables() {
            vars.insert(variable);
        }
    }
    vars.into_iter().collect()
}

/// `formula` holds under every assignment of its alphabet.
pub fn is_valid(formula: &Formula) -> Validity {
    let universe = formula.variables();
    match find_counterexample(&universe, |assignment| evaluate(formula, assignment)) {
        Some(counterexample) => Validity::Invalid(counterexample),
        None => Validity::Valid,
    }
}

/// `formula` has at least one satisfying assignment.
pub fn is_satisfiable(formula: &Formula) -> Option<BTreeMap<String, bool>> {
    let universe = formula.variables();
    find_counterexample(&universe, |assignment| !evaluate(formula, assignment))
}

/// Implication `left -> right` is a tautology. Returns a counterexample to
/// the implication when invalid.
pub fn check_implication(
    left: &Formula,
    right: &Formula,
) -> Result<(), BTreeMap<String, bool>> {
    let universe = sorted_universe(&[left, right]);
    let counterexample = find_counterexample(&universe, |assignment| {
        !evaluate(left, assignment) || evaluate(right, assignment)
    });
    match counterexample {
        Some(assignment) => Err(assignment),
        None => Ok(()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ipc_syntax::parse;

    #[test]
    fn recognises_tautology_and_contradiction() {
        assert_eq!(is_valid(&parse("p | !p").unwrap()), Validity::Valid);
        match is_valid(&parse("p & !p").unwrap()) {
            Validity::Invalid(assignment) => {
                assert!(assignment.contains_key("p"));
            }
            Validity::Valid => panic!("contradiction is not valid"),
        }
    }

    #[test]
    fn implication_failure_returns_witness() {
        let left = parse("p").unwrap();
        let right = parse("q").unwrap();
        let witness = match check_implication(&left, &right) {
            Err(witness) => witness,
            Ok(()) => panic!("p must not imply q"),
        };
        assert_eq!(witness.get("p"), Some(&true));
        assert_eq!(witness.get("q"), Some(&false));
    }
}
