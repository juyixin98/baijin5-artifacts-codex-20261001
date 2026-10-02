//! Small brute-force witness finder for clauses that survive elimination.
//!
//! Surviving clauses only mention variables that were skipped (pure for one
//! side at elimination time); their original clauses were already removed.
//! We still need a genuine model, so solve the surviving clause set directly
//! with chronological backtracking over a small variable universe.

use ipc_proof::{Clause, Literal};
use std::collections::BTreeMap;

pub fn find_model(
    clauses: &[Clause],
    variables: &[String],
) -> Option<BTreeMap<String, bool>> {
    let mut assignment: BTreeMap<String, bool> = BTreeMap::new();
    if solve(clauses, variables, 0, &mut assignment) {
        Some(assignment)
    } else {
        None
    }
}

fn solve(
    clauses: &[Clause],
    variables: &[String],
    index: usize,
    assignment: &mut BTreeMap<String, bool>,
) -> bool {
    if index == variables.len() {
        return clauses
            .iter()
            .all(|clause| clause_satisfied(clause, assignment));
    }
    for value in [false, true] {
        assignment.insert(variables[index].clone(), value);
        if !prune(clauses, assignment)
            && solve(clauses, variables, index + 1, assignment)
        {
            return true;
        }
    }
    assignment.remove(&variables[index]);
    false
}

fn prune(clauses: &[Clause], assignment: &BTreeMap<String, bool>) -> bool {
    clauses.iter().any(|clause| {
        clause.literals.iter().all(|literal| {
            match assignment.get(&literal.variable) {
                Some(value) => *value != literal.positive,
                None => false,
            }
        })
    })
}

fn clause_satisfied(clause: &Clause, assignment: &BTreeMap<String, bool>) -> bool {
    clause.literals.iter().any(|literal: &Literal| {
        assignment
            .get(&literal.variable)
            .copied()
            .unwrap_or(false)
            == literal.positive
    })
}
