//! Error contract shared by all modules.
//!
//! Every fallible operation in this crate returns [`QeError`], whose `kind`
//! classifies the failure into one of four distinguishable categories:
//!
//! * [`ErrorKind::InvalidInput`] - malformed JSON, unknown predicate or
//!   constant, arity mismatch, tuple referencing a non-domain element.
//! * [`ErrorKind::StateConflict`] - the model/formula combination violates a
//!   declared policy or state requirement, e.g. an empty domain while
//!   `allow_empty_domain` is false, duplicate domain elements, or a formula
//!   with unbound free variables.
//! * [`ErrorKind::ResourceExhausted`] - a configured budget or a recursion
//!   depth limit was exceeded.
//! * [`ErrorKind::ComputationFailed`] - an internal invariant or an
//!   independent verification step failed.

use serde::Serialize;
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorKind {
    InvalidInput,
    StateConflict,
    ResourceExhausted,
    ComputationFailed,
}

impl ErrorKind {
    /// Stable machine-readable tag used in JSON reports and logs.
    pub fn as_str(self) -> &'static str {
        match self {
            ErrorKind::InvalidInput => "invalid_input",
            ErrorKind::StateConflict => "state_conflict",
            ErrorKind::ResourceExhausted => "resource_exhausted",
            ErrorKind::ComputationFailed => "computation_failed",
        }
    }

    /// Process exit code used by the CLI so scripts can distinguish failures.
    pub fn exit_code(self) -> i32 {
        match self {
            ErrorKind::InvalidInput => 2,
            ErrorKind::StateConflict => 3,
            ErrorKind::ResourceExhausted => 4,
            ErrorKind::ComputationFailed => 5,
        }
    }
}

impl fmt::Display for ErrorKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct QeError {
    pub kind: ErrorKind,
    pub message: String,
}

impl QeError {
    pub fn new(kind: ErrorKind, message: impl Into<String>) -> Self {
        QeError {
            kind,
            message: message.into(),
        }
    }

    pub fn invalid_input(message: impl Into<String>) -> Self {
        QeError::new(ErrorKind::InvalidInput, message)
    }

    pub fn state_conflict(message: impl Into<String>) -> Self {
        QeError::new(ErrorKind::StateConflict, message)
    }

    pub fn resource_exhausted(message: impl Into<String>) -> Self {
        QeError::new(ErrorKind::ResourceExhausted, message)
    }

    pub fn computation_failed(message: impl Into<String>) -> Self {
        QeError::new(ErrorKind::ComputationFailed, message)
    }

    pub fn exit_code(&self) -> i32 {
        self.kind.exit_code()
    }
}

impl fmt::Display for QeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "[{}] {}", self.kind, self.message)
    }
}

impl std::error::Error for QeError {}
