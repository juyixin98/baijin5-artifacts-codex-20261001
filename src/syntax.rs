//! Logical syntax: propositions, literals and clauses.
//!
//! A literal is a signed integer: positive `p` is variable `p`, negative `-p`
//! is its negation. Variable ids are required to be strictly positive. A clause
//! is a disjunction of literals, represented as a deduplicated, sorted vector
//! so that comparison of derived/resolvent clauses is canonical.

use std::cmp::Ordering;
use std::collections::BTreeSet;

/// One literal: non-zero variable id plus polarity.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct Literal {
    pub var: u32,
    pub positive: bool,
}

impl Literal {
    /// Construct from a signed DIMACS-style integer.
    ///
    /// `0` is rejected because, in DIMACS, `0` terminates a clause rather than
    /// naming a variable.
    pub fn from_signed(raw: i64) -> Result<Self, SyntaxError> {
        if raw == 0 {
            return Err(SyntaxError::ZeroLiteral);
        }
        let abs = raw.unsigned_abs();
        if abs > u32::MAX as u64 {
            return Err(SyntaxError::VariableOutOfRange(raw));
        }
        Ok(Literal {
            var: abs as u32,
            positive: raw > 0,
        })
    }

    /// DIMACS-style signed encoding.
    pub fn to_signed(self) -> i64 {
        if self.positive {
            self.var as i64
        } else {
            -(self.var as i64)
        }
    }

    /// The complementary literal: same variable, opposite polarity.
    pub fn negate(self) -> Self {
        Literal {
            var: self.var,
            positive: !self.positive,
        }
    }
}

impl PartialOrd for Literal {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for Literal {
    fn cmp(&self, other: &Self) -> Ordering {
        self.var.cmp(&other.var).then_with(|| {
            self.positive
                .cmp(&other.positive)
        })
    }
}

/// A disjunction of literals, canonically normalized (sorted, de-duplicated).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Clause {
    lits: Vec<Literal>,
}

impl Clause {
    /// Build from raw signed integers, applying normalization.
    pub fn from_signed(raws: &[i64]) -> Result<Self, SyntaxError> {
        let mut set = BTreeSet::new();
        for &r in raws {
            set.insert(Literal::from_signed(r)?);
        }
        Ok(Clause {
            lits: set.into_iter().collect(),
        })
    }

    /// Build from already-validated literals; sorts and de-duplicates.
    pub fn from_literals<I: IntoIterator<Item = Literal>>(iter: I) -> Self {
        let set: BTreeSet<Literal> = iter.into_iter().collect();
        Clause {
            lits: set.into_iter().collect(),
        }
    }

    pub fn literals(&self) -> &[Literal] {
        &self.lits
    }

    pub fn is_empty(&self) -> bool {
        self.lits.is_empty()
    }

    pub fn len(&self) -> usize {
        self.lits.len()
    }

    pub fn contains(&self, lit: Literal) -> bool {
        self.lits.binary_search(&lit).is_ok()
    }

    /// DIMACS-style signed form, for rendering and fixtures.
    pub fn to_signed(&self) -> Vec<i64> {
        self.lits.iter().map(|l| l.to_signed()).collect()
    }
}

/// Errors that can occur while parsing the logical syntax.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SyntaxError {
    ZeroLiteral,
    VariableOutOfRange(i64),
}

impl std::fmt::Display for SyntaxError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SyntaxError::ZeroLiteral => {
                write!(f, "literal 0 is reserved as a clause terminator")
            }
            SyntaxError::VariableOutOfRange(v) => {
                write!(f, "variable id {v} is out of range (max {})", u32::MAX)
            }
        }
    }
}

impl std::error::Error for SyntaxError {}
