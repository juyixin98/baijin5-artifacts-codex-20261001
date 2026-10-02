//! Literals and clauses in conjunctive normal form.

use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

/// Signed propositional variable. `positive == true` means `var`, else `!var`.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct Literal {
    pub variable: String,
    pub positive: bool,
}

impl Literal {
    pub fn positive(variable: impl Into<String>) -> Self {
        Literal {
            variable: variable.into(),
            positive: true,
        }
    }

    pub fn negative(variable: impl Into<String>) -> Self {
        Literal {
            variable: variable.into(),
            positive: false,
        }
    }

    pub fn negate(&self) -> Self {
        Literal {
            variable: self.variable.clone(),
            positive: !self.positive,
        }
    }

    pub fn render(&self) -> String {
        if self.positive {
            self.variable.clone()
        } else {
            format!("!{}", self.variable)
        }
    }
}

impl std::fmt::Display for Literal {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.render())
    }
}

/// A clause is a disjunction of literals kept as a sorted, deduplicated vector.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct Clause {
    pub literals: Vec<Literal>,
}

impl Clause {
    pub fn new(mut literals: Vec<Literal>) -> Self {
        literals.sort();
        literals.dedup();
        Clause { literals }
    }

    pub fn empty() -> Self {
        Clause {
            literals: Vec::new(),
        }
    }

    pub fn unit(literal: Literal) -> Self {
        Clause {
            literals: vec![literal],
        }
    }

    pub fn is_empty(&self) -> bool {
        self.literals.is_empty()
    }

    pub fn is_tautology(&self) -> bool {
        let mut seen: BTreeSet<&str> = BTreeSet::new();
        for lit in &self.literals {
            if self
                .literals
                .iter()
                .any(|other| other.variable == lit.variable && other.positive != lit.positive)
            {
                return true;
            }
            seen.insert(lit.variable.as_str());
        }
        false
    }

    pub fn contains(&self, literal: &Literal) -> bool {
        self.literals.binary_search(literal).is_ok()
    }

    /// Resolve two clauses on `variable`.
    ///
    /// Returns `None` if the required complementary literals are missing or
    /// the resolvent is a tautology. The resulting clause removes every
    /// occurrence of either polarity of the pivot variable.
    pub fn resolve(&self, other: &Clause, variable: &str) -> Option<Clause> {
        let pos_here = self.literals.iter().any(|l| l.variable == variable && l.positive);
        let neg_here = self
            .literals
            .iter()
            .any(|l| l.variable == variable && !l.positive);
        let pos_other = other
            .literals
            .iter()
            .any(|l| l.variable == variable && l.positive);
        let neg_other = other
            .literals
            .iter()
            .any(|l| l.variable == variable && !l.positive);
        if !((pos_here && neg_other) || (neg_here && pos_other)) {
            return None;
        }
        let mut lits: Vec<Literal> = Vec::new();
        for literal in self.literals.iter().chain(other.literals.iter()) {
            if literal.variable != variable {
                lits.push(literal.clone());
            }
        }
        let clause = Clause::new(lits);
        if clause.is_tautology() { None } else { Some(clause) }
    }

    pub fn variables(&self) -> Vec<String> {
        let vars: BTreeSet<String> = self.literals.iter().map(|l| l.variable.clone()).collect();
        vars.into_iter().collect()
    }

    pub fn render(&self) -> String {
        if self.literals.is_empty() {
            "()".to_string()
        } else {
            self.literals
                .iter()
                .map(Literal::render)
                .collect::<Vec<_>>()
                .join(" | ")
        }
    }
}

impl std::fmt::Display for Clause {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.render())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolves_on_pivot() {
        let left = Clause::new(vec![
            Literal::positive("a"),
            Literal::positive("b"),
        ]);
        let right = Clause::new(vec![
            Literal::negative("a"),
            Literal::positive("c"),
        ]);
        assert_eq!(
            left.resolve(&right, "a"),
            Some(Clause::new(vec![
                Literal::positive("b"),
                Literal::positive("c")
            ]))
        );
    }

    #[test]
    fn rejects_invalid_resolution_and_tautology() {
        let left = Clause::new(vec![Literal::positive("a")]);
        let right = Clause::new(vec![Literal::positive("b")]);
        assert!(left.resolve(&right, "a").is_none());
        let taut = Clause::new(vec![Literal::positive("a"), Literal::negative("a")]);
        assert!(taut.is_tautology());
    }
}
