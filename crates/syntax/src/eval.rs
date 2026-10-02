//! Truth assignment and semantic evaluation.

use crate::formula::Formula;
use std::collections::BTreeMap;

/// A total assignment for a fixed universe of variables.
#[derive(Debug, Clone)]
pub struct Assignment<'a> {
    pub values: &'a BTreeMap<String, bool>,
}

impl<'a> Assignment<'a> {
    pub fn new(values: &'a BTreeMap<String, bool>) -> Self {
        Assignment { values }
    }

    pub fn lookup(&self, name: &str) -> bool {
        *self.values.get(name).unwrap_or(&false)
    }
}

/// Evaluate a formula under a truth assignment. Unknown variables read as `false`.
pub fn evaluate(formula: &Formula, values: &BTreeMap<String, bool>) -> bool {
    let assignment = Assignment::new(values);
    eval_with(formula, &assignment)
}

pub fn eval_with(formula: &Formula, assignment: &Assignment<'_>) -> bool {
    match formula {
        Formula::Const(v) => *v,
        Formula::Var(name) => assignment.lookup(name),
        Formula::Not(f) => !eval_with(f, assignment),
        Formula::And(xs) => xs.iter().all(|f| eval_with(f, assignment)),
        Formula::Or(xs) => xs.iter().any(|f| eval_with(f, assignment)),
        Formula::Imp(a, b) => !eval_with(a, assignment) || eval_with(b, assignment),
    }
}
