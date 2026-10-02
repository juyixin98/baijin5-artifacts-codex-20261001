//! Propositional formula abstract syntax tree and basic algebra.

use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

/// Propositional formula.
///
/// Supported syntax:
/// - constants: `true`, `false`
/// - variables: identifier starting with a letter or `_`
/// - negation: `!p`
/// - conjunction: `p & q`
/// - disjunction: `p | q`
/// - implication: `p -> q`
/// - grouping: `(...)`
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Formula {
    Const(bool),
    Var(String),
    Not(Box<Formula>),
    And(Vec<Formula>),
    Or(Vec<Formula>),
    Imp(Box<Formula>, Box<Formula>),
}

impl Formula {
    pub fn var(name: impl Into<String>) -> Self {
        Formula::Var(name.into())
    }

    pub fn not(inner: Formula) -> Self {
        Formula::Not(Box::new(inner))
    }

    pub fn and(items: Vec<Formula>) -> Self {
        if items.len() == 1 {
            items.into_iter().next().unwrap()
        } else {
            Formula::And(items)
        }
    }

    pub fn or(items: Vec<Formula>) -> Self {
        if items.len() == 1 {
            items.into_iter().next().unwrap()
        } else {
            Formula::Or(items)
        }
    }

    pub fn imp(left: Formula, right: Formula) -> Self {
        Formula::Imp(Box::new(left), Box::new(right))
    }

    /// Collect every variable name occurring in the formula, sorted and unique.
    pub fn variables(&self) -> Vec<String> {
        let mut acc = BTreeSet::new();
        collect_vars(self, &mut acc);
        acc.into_iter().collect()
    }

    pub fn contains_var(&self, name: &str) -> bool {
        match self {
            Formula::Const(_) => false,
            Formula::Var(v) => v == name,
            Formula::Not(f) => f.contains_var(name),
            Formula::And(xs) | Formula::Or(xs) => xs.iter().any(|f| f.contains_var(name)),
            Formula::Imp(a, b) => a.contains_var(name) || b.contains_var(name),
        }
    }

    /// Render with fully parenthesized, unambiguous notation.
    pub fn to_pretty(&self) -> String {
        match self {
            Formula::Const(true) => "true".to_string(),
            Formula::Const(false) => "false".to_string(),
            Formula::Var(v) => v.clone(),
            Formula::Not(f) => format!("!{}", atom_or_pretty(f)),
            Formula::And(xs) => xs
                .iter()
                .map(atom_or_pretty)
                .collect::<Vec<_>>()
                .join(" & "),
            Formula::Or(xs) => xs
                .iter()
                .map(atom_or_pretty)
                .collect::<Vec<_>>()
                .join(" | "),
            Formula::Imp(a, b) => format!("({} -> {})", a.to_pretty(), b.to_pretty()),
        }
    }
}

fn atom_or_pretty(f: &Formula) -> String {
    match f {
        Formula::Var(_) | Formula::Const(_) => f.to_pretty(),
        Formula::Not(inner) => format!("!{}", atom_or_pretty(inner)),
        other => format!("({})", other.to_pretty()),
    }
}

fn collect_vars(formula: &Formula, acc: &mut BTreeSet<String>) {
    match formula {
        Formula::Const(_) => {}
        Formula::Var(v) => {
            acc.insert(v.clone());
        }
        Formula::Not(f) => collect_vars(f, acc),
        Formula::And(xs) | Formula::Or(xs) => {
            for f in xs {
                collect_vars(f, acc);
            }
        }
        Formula::Imp(a, b) => {
            collect_vars(a, acc);
            collect_vars(b, acc);
        }
    }
}

impl std::fmt::Display for Formula {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.to_pretty())
    }
}
