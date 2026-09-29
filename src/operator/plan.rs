//! Query operator plan: exactly two range predicates over two typed batches.

use serde::{Deserialize, Serialize};

use crate::error::{ErrorCode, JoinError, JoinResult};

/// The four inequality directions. All are read as
/// `left_column OP right_column`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Comparator {
    Lt,
    Le,
    Gt,
    Ge,
}

impl Comparator {
    pub fn as_str(self) -> &'static str {
        match self {
            Comparator::Lt => "<",
            Comparator::Le => "<=",
            Comparator::Gt => ">",
            Comparator::Ge => ">=",
        }
    }

    /// True for `<` and `>`: equal-key rows must NOT match.
    pub fn is_strict(self) -> bool {
        matches!(self, Comparator::Lt | Comparator::Gt)
    }
}

/// One predicate `left[left_col] OP right[right_col]`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Predicate {
    pub left_col: usize,
    pub right_col: usize,
    pub op: Comparator,
}

impl Predicate {
    pub fn validate(&self, left_width: usize, right_width: usize) -> JoinResult<()> {
        if self.left_col >= left_width {
            return Err(JoinError::input(
                ErrorCode::InvalidPlan,
                format!(
                    "predicate left_col {} out of range (0..{})",
                    self.left_col, left_width
                ),
            ));
        }
        if self.right_col >= right_width {
            return Err(JoinError::input(
                ErrorCode::InvalidPlan,
                format!(
                    "predicate right_col {} out of range (0..{})",
                    self.right_col, right_width
                ),
            ));
        }
        Ok(())
    }
}

/// A two-predicate range join: both predicates must hold (AND semantics).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct JoinPlan {
    pub p1: Predicate,
    pub p2: Predicate,
}

impl JoinPlan {
    pub fn new(p1: Predicate, p2: Predicate) -> Self {
        Self { p1, p2 }
    }

    pub fn validate(&self, left_width: usize, right_width: usize) -> JoinResult<()> {
        self.p1.validate(left_width, right_width)?;
        self.p2.validate(left_width, right_width)?;
        if self.p1.left_col == self.p2.left_col && self.p1.right_col == self.p2.right_col {
            return Err(JoinError::input(
                ErrorCode::DuplicateBinding,
                "the two predicates bind the identical column pair; expected two range predicates",
            ));
        }
        Ok(())
    }

    /// Both predicates in a canonical `smaller_key <|<= greater_key` form.
    pub fn canonical(&self) -> (CanonicalPredicate, CanonicalPredicate) {
        ((&self.p1).into(), (&self.p2).into())
    }
}

/// Predicate expressed independent of physical side placement:
/// `smaller_key <|<= greater_key`, with a flag saying which input is smaller.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CanonicalPredicate {
    /// True when the *left* batch supplies the smaller-key operand.
    pub smaller_is_left: bool,
    pub strict: bool,
    pub smaller_col: usize,
    pub greater_col: usize,
}

impl From<&Predicate> for CanonicalPredicate {
    fn from(p: &Predicate) -> Self {
        match p.op {
            Comparator::Lt | Comparator::Le => CanonicalPredicate {
                smaller_is_left: true,
                strict: p.op.is_strict(),
                smaller_col: p.left_col,
                greater_col: p.right_col,
            },
            Comparator::Gt | Comparator::Ge => CanonicalPredicate {
                smaller_is_left: false,
                strict: p.op.is_strict(),
                smaller_col: p.right_col,
                greater_col: p.left_col,
            },
        }
    }
}
