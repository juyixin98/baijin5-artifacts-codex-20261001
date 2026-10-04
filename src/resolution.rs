//! Inference core: the propositional binary resolution rule.
//!
//! Given parent clauses `A` and `B` and a pivot variable `p`, a single
//! resolution step is legal exactly when:
//!
//! 1. `A` contains the positive pivot literal `p` and does not contain `-p`;
//! 2. `B` contains the negative pivot literal `-p` and does not contain `p`;
//! 3. the resolvent is the union of the two clauses with both pivot literals
//!    removed.
//!
//! The polarity-strict condition on each parent is an explicit algorithm
//! assumption: a tautological parent that already contains both signs of the
//! pivot cannot meaningfully "eliminate" it in one step, and is rejected rather
//! than silently accepted. This makes single-step tampering detectable.

use crate::syntax::{Clause, Literal};

/// Why a proposed resolution step is not legal.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ResolutionError {
    /// The positive pivot is missing from the (left) parent.
    MissingPositivePivot { var: u32 },
    /// The negative pivot is missing from the (right) parent.
    MissingNegativePivot { var: u32 },
    /// A parent contains both signs of the pivot (tautological on pivot).
    ParentContainsBothPolarities { var: u32, parent: ParentSide },
    /// The claimed resolvent does not equal the uniquely determined resolvent.
    ResolventMismatch {
        expected: Vec<i64>,
        claimed: Vec<i64>,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ParentSide {
    Left,
    Right,
}

impl std::fmt::Display for ParentSide {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ParentSide::Left => write!(f, "left"),
            ParentSide::Right => write!(f, "right"),
        }
    }
}

impl std::fmt::Display for ResolutionError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ResolutionError::MissingPositivePivot { var } => {
                write!(f, "left parent does not contain positive pivot +{var}")
            }
            ResolutionError::MissingNegativePivot { var } => {
                write!(f, "right parent does not contain negative pivot -{var}")
            }
            ResolutionError::ParentContainsBothPolarities { var, parent } => {
                write!(
                    f,
                    "{parent} parent contains both polarities of pivot {var}"
                )
            }
            ResolutionError::ResolventMismatch { expected, claimed } => {
                write!(
                    f,
                    "claimed resolvent {:?} does not match legal resolvent {:?}",
                    claimed, expected
                )
            }
        }
    }
}

impl std::error::Error for ResolutionError {}

/// Compute the legal resolvent of `left` and `right` on pivot variable `var`
/// without checking a claimed result. Returns `None` if the pivot elimination
/// is not legal.
pub fn resolve(left: &Clause, right: &Clause, var: u32) -> Option<Clause> {
    let pos = Literal {
        var,
        positive: true,
    };
    let neg = Literal {
        var,
        positive: false,
    };

    if !left.contains(pos) || left.contains(neg) {
        return None;
    }
    if !right.contains(neg) || right.contains(pos) {
        return None;
    }

    let lits = left
        .literals()
        .iter()
        .chain(right.literals().iter())
        .copied()
        .filter(|l| l.var != var);
    Some(Clause::from_literals(lits))
}

/// Validate a complete resolution step, including that the caller's claimed
/// resolvent is exactly the clause the rule produces.
pub fn validate_step(
    left: &Clause,
    right: &Clause,
    var: u32,
    claimed: &Clause,
) -> Result<Clause, ResolutionError> {
    let pos = Literal {
        var,
        positive: true,
    };
    let neg = Literal {
        var,
        positive: false,
    };

    if left.contains(pos) && left.contains(neg) {
        return Err(ResolutionError::ParentContainsBothPolarities {
            var,
            parent: ParentSide::Left,
        });
    }
    if right.contains(pos) && right.contains(neg) {
        return Err(ResolutionError::ParentContainsBothPolarities {
            var,
            parent: ParentSide::Right,
        });
    }
    if !left.contains(pos) {
        return Err(ResolutionError::MissingPositivePivot { var });
    }
    if !right.contains(neg) {
        return Err(ResolutionError::MissingNegativePivot { var });
    }

    let computed = resolve(left, right, var)
        .expect("pivot preconditions checked above");
    if &computed != claimed {
        return Err(ResolutionError::ResolventMismatch {
            expected: computed.to_signed(),
            claimed: claimed.to_signed(),
        });
    }
    Ok(computed)
}
