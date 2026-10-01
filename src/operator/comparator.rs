//! Predicate comparison operators.
//!
//! The IEJoin algorithm sorts each side of each predicate once. It only
//! needs two *sort senses* (ascending vs descending), but the join
//! condition itself has four inequality directions. The distinction that
//! matters algorithmically is strict (`<`, `>`) vs non-strict
//! (`<=`, `>=`): equal values land on different sides of the boundary.
//!
//! NULL handling lives here too: every comparison involving a NULL
//! returns false (so NULL rows never join).

use serde::{Deserialize, Serialize};

use crate::error::{JoinError, JoinResult};

/// One of the four inequality directions supported on a predicate.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Comparator {
    Lt,
    Le,
    Gt,
    Ge,
}

impl Comparator {
    /// Parse the textual forms `<`, `<=`, `>`, `>=`.
    pub fn parse(s: &str) -> JoinResult<Self> {
        match s.trim() {
            "<" => Ok(Self::Lt),
            "<=" => Ok(Self::Le),
            ">" => Ok(Self::Gt),
            ">=" => Ok(Self::Ge),
            other => Err(JoinError::input(
                "unsupported_comparator",
                format!("comparator '{other}' is not one of <, <=, >, >="),
            )),
        }
    }

    /// Whether equality satisfies the predicate.
    #[must_use]
    pub fn is_strict(self) -> bool {
        matches!(self, Self::Lt | Self::Gt)
    }

    /// Whether the right side is required to be greater than the left.
    #[must_use]
    pub fn right_greater(self) -> bool {
        matches!(self, Self::Lt | Self::Le)
    }

    /// Evaluate `left CMP right` under SQL three-valued logic.
    /// `None` on either side yields `false`.
    #[must_use]
    pub fn eval(self, left: Option<i64>, right: Option<i64>) -> bool {
        match (left, right) {
            (Some(l), Some(r)) => match self {
                Self::Lt => l < r,
                Self::Le => l <= r,
                Self::Gt => l > r,
                Self::Ge => l >= r,
            },
            _ => false,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::Comparator;
    use super::Comparator::*;

    #[test]
    fn null_never_matches() {
        for c in [Lt, Le, Gt, Ge] {
            assert!(!c.eval(None, Some(1)));
            assert!(!c.eval(Some(1), None));
            assert!(!c.eval(None, None));
        }
    }

    #[test]
    fn strict_boundary_excludes_equals() {
        assert!(Lt.eval(Some(1), Some(2)));
        assert!(!Lt.eval(Some(2), Some(2)));
        assert!(Le.eval(Some(2), Some(2)));
        assert!(Gt.eval(Some(2), Some(1)));
        assert!(!Gt.eval(Some(2), Some(2)));
        assert!(Ge.eval(Some(2), Some(2)));
    }

    #[test]
    fn parser_rejects_equality() {
        assert!(Comparator::parse("=").is_err());
        assert!(Comparator::parse("!=").is_err());
        assert!(Comparator::parse("<=").is_ok());
    }
}
