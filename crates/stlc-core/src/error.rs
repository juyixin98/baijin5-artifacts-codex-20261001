//! Failure categories, kept distinct on purpose:
//!
//! * malformed input (`parse`),
//! * typing violations (`type_error`),
//! * exhausted reduction budget (`budget_exhausted`),
//! * internal invariants (`internal`).

use serde::{Deserialize, Serialize};
use stlc_syntax::Type;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "category", rename_all = "snake_case")]
pub enum TypeError {
    UnknownFreeVar { name: String },
    ExpectedFunction { found: Type },
    DomainMismatch { expected: Type, found: Type },
    IfGuardNotBool { found: Type },
    BranchMismatch { then_ty: Type, else_ty: Type },
    /// A dangling de Bruijn index reached the checker. Inputs converted by the
    /// syntax layer never trigger this; it guards programmatic callers.
    DanglingIndex { index: usize, depth: usize },
}

impl std::fmt::Display for TypeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TypeError::UnknownFreeVar { name } => {
                write!(f, "unknown free variable `{name}` (no declared type)")
            }
            TypeError::ExpectedFunction { found } => {
                write!(f, "function position has non-function type {found:?}")
            }
            TypeError::DomainMismatch { expected, found } => write!(
                f,
                "argument type {found:?} does not match domain {expected:?}"
            ),
            TypeError::IfGuardNotBool { found } => {
                write!(f, "if-guard has type {found:?}, expected Bool")
            }
            TypeError::BranchMismatch { then_ty, else_ty } => write!(
                f,
                "branches disagree: then is {then_ty:?}, else is {else_ty:?}"
            ),
            TypeError::DanglingIndex { index, depth } => write!(
                f,
                "dangling bound index {index} at depth {depth}"
            ),
        }
    }
}

impl std::error::Error for TypeError {}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "category", rename_all = "snake_case")]
pub enum NormError {
    Type(TypeError),
    BudgetExhausted {
        limit: usize,
        steps_used: usize,
        /// Last reduction state, for diagnostics.
        last_term_db: String,
    },
    Internal {
        detail: String,
    },
}

impl std::fmt::Display for NormError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            NormError::Type(e) => write!(f, "{e}"),
            NormError::BudgetExhausted {
                limit, steps_used, ..
            } => write!(
                f,
                "reduction budget exhausted after {steps_used}/{limit} steps"
            ),
            NormError::Internal { detail } => write!(f, "internal invariant failure: {detail}"),
        }
    }
}

impl std::error::Error for NormError {}

impl From<TypeError> for NormError {
    fn from(value: TypeError) -> Self {
        NormError::Type(value)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "category", rename_all = "snake_case")]
pub enum DriverError {
    Parse { message: String, offset: usize },
    Check(NormError),
}

impl std::fmt::Display for DriverError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DriverError::Parse { message, offset } => {
                write!(f, "parse error at byte {offset}: {message}")
            }
            DriverError::Check(e) => write!(f, "{e}"),
        }
    }
}

impl std::error::Error for DriverError {}
