//! Error taxonomy for the query engine.
//!
//! Every failure crossing a module boundary is a [`QueryError`]. The four
//! non-cancellation categories are deliberately disjoint so callers (and
//! tests) can distinguish *why* an execution failed:
//!
//! | Category            | Meaning                                             |
//! |---------------------|-----------------------------------------------------|
//! | `Input`             | Bad user input rejected at the validation entry     |
//! | `StateConflict`     | Operator protocol violation (poll after close/err)  |
//! | `ResourceExhausted` | Memory budget / spill quota / row limit exceeded    |
//! | `Compute`           | A computation or IO step failed mid-execution       |
//! | `Cancelled`         | Execution was cancelled — user vs timeout is typed  |

use serde::Serialize;

/// Why an execution was cancelled. User cancellation and deadline expiry are
/// distinct values so downstream code never has to guess.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CancelKind {
    /// Explicitly requested by the user (e.g. `POST /query/{id}/cancel`).
    User,
    /// The query deadline elapsed.
    Timeout,
}

/// Stable, machine-readable error category. Used in HTTP responses and test
/// assertions.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCategory {
    Input,
    StateConflict,
    ResourceExhausted,
    Compute,
    CancelledUser,
    CancelledTimeout,
}

#[derive(Debug, thiserror::Error)]
pub enum QueryError {
    #[error("invalid input: {0}")]
    Input(String),
    #[error("state conflict: {0}")]
    StateConflict(String),
    #[error("resource exhausted: {0}")]
    ResourceExhausted(String),
    #[error("compute failure: {0}")]
    Compute(String),
    #[error("cancelled: {kind:?}")]
    Cancelled { kind: CancelKind },
}

impl QueryError {
    pub fn input(msg: impl Into<String>) -> Self {
        Self::Input(msg.into())
    }
    pub fn state_conflict(msg: impl Into<String>) -> Self {
        Self::StateConflict(msg.into())
    }
    pub fn resource(msg: impl Into<String>) -> Self {
        Self::ResourceExhausted(msg.into())
    }
    pub fn compute(msg: impl Into<String>) -> Self {
        Self::Compute(msg.into())
    }
    pub fn cancelled(kind: CancelKind) -> Self {
        Self::Cancelled { kind }
    }

    pub fn category(&self) -> ErrorCategory {
        match self {
            Self::Input(_) => ErrorCategory::Input,
            Self::StateConflict(_) => ErrorCategory::StateConflict,
            Self::ResourceExhausted(_) => ErrorCategory::ResourceExhausted,
            Self::Compute(_) => ErrorCategory::Compute,
            Self::Cancelled { kind: CancelKind::User } => ErrorCategory::CancelledUser,
            Self::Cancelled { kind: CancelKind::Timeout } => ErrorCategory::CancelledTimeout,
        }
    }
}

impl From<arrow2::error::Error> for QueryError {
    fn from(e: arrow2::error::Error) -> Self {
        Self::Compute(format!("arrow2: {e}"))
    }
}

impl From<std::io::Error> for QueryError {
    fn from(e: std::io::Error) -> Self {
        Self::Compute(format!("io: {e}"))
    }
}
