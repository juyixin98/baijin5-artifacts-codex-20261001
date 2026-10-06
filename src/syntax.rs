//! Logical syntax: literals and clauses over propositional variables.
//!
//! Clauses are kept in normalized form (sorted by literal, no duplicates).
//! Duplicate literals in raw input are *rejected*, not silently merged, so
//! that a proof author cannot smuggle a different clause past the checker.

use std::fmt;

/// A propositional literal: a variable with a polarity.
#[derive(Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Debug)]
pub struct Literal {
    pub var: u32,
    pub positive: bool,
}

impl Literal {
    pub fn new(var: u32, positive: bool) -> Self {
        Literal { var, positive }
    }

    pub fn negate(self) -> Self {
        Literal {
            var: self.var,
            positive: !self.positive,
        }
    }

    /// Parse a DIMACS-style signed integer (`0` is the clause terminator and
    /// is rejected here; the caller handles terminators).
    pub fn from_dimacs(raw: i64) -> Result<Literal, SyntaxError> {
        if raw == 0 {
            return Err(SyntaxError::ZeroLiteral);
        }
        let var = raw.unsigned_abs();
        if var > u32::MAX as u64 {
            return Err(SyntaxError::VarOutOfRange(raw));
        }
        Ok(Literal::new(var as u32, raw > 0))
    }

    /// DIMACS-style signed integer representation.
    pub fn to_dimacs(self) -> i64 {
        if self.positive {
            self.var as i64
        } else {
            -(self.var as i64)
        }
    }
}

impl fmt::Display for Literal {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.to_dimacs())
    }
}

/// Errors detected while building syntax objects from raw input.
#[derive(Clone, PartialEq, Eq, Debug)]
pub enum SyntaxError {
    ZeroLiteral,
    VarOutOfRange(i64),
    DuplicateLiteral(u32),
}

impl fmt::Display for SyntaxError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            SyntaxError::ZeroLiteral => write!(f, "literal 0 is only a terminator"),
            SyntaxError::VarOutOfRange(v) => write!(f, "variable {v} out of range"),
            SyntaxError::DuplicateLiteral(v) => {
                write!(f, "duplicate literal on variable {v}")
            }
        }
    }
}

/// A clause: a set of literals, stored sorted and duplicate-free.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct Clause {
    lits: Vec<Literal>,
}

impl Clause {
    /// Build a normalized clause from raw literals.
    ///
    /// Rejects duplicate variables (in either polarity): `x v x` and
    /// `x v ~x` inside a *declared* clause are both malformed input for this
    /// checker, because the proof format requires authors to write clauses
    /// in normal form.
    pub fn from_raw(mut lits: Vec<Literal>) -> Result<Clause, SyntaxError> {
        lits.sort();
        for w in lits.windows(2) {
            if w[0].var == w[1].var {
                return Err(SyntaxError::DuplicateLiteral(w[0].var));
            }
        }
        Ok(Clause { lits })
    }

    pub fn empty() -> Clause {
        Clause { lits: Vec::new() }
    }

    pub fn lits(&self) -> &[Literal] {
        &self.lits
    }

    pub fn len(&self) -> usize {
        self.lits.len()
    }

    pub fn is_empty(&self) -> bool {
        self.lits.is_empty()
    }

    pub fn contains(&self, lit: Literal) -> bool {
        self.lits.binary_search(&lit).is_ok()
    }

    pub fn contains_var(&self, var: u32) -> bool {
        self.lits.iter().any(|l| l.var == var)
    }

    /// DIMACS-style rendering, e.g. `1 -2 0`; the empty clause is `0`.
    pub fn to_dimacs_string(&self) -> String {
        let mut s = String::new();
        for lit in &self.lits {
            s.push_str(&lit.to_string());
            s.push(' ');
        }
        s.push('0');
        s
    }
}

impl fmt::Display for Clause {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.to_dimacs_string())
    }
}
