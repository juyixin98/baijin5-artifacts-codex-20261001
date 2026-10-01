//! Error contract shared by every module in the crate.
//!
//! Failures are partitioned into four disjoint classes so that callers can
//! react differently to bad input, inconsistent state, exhausted budgets and
//! genuine computation failures.

use serde::{Deserialize, Serialize};

/// The high level category of a failure.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorKind {
    /// Malformed request, unknown symbols, arity/type errors, unreadable file.
    Input,
    /// Two pieces of state disagree (proof record vs model vs formula).
    StateConflict,
    /// A bounded resource was exhausted before the computation finished.
    ResourceExhausted,
    /// The input was well formed but evaluation cannot produce a value.
    ComputationFailed,
}

/// Structured error value used across library and service boundary.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct QeError {
    pub kind: ErrorKind,
    /// Stable machine readable code, e.g. `unknown_symbol`.
    pub code: String,
    /// Human readable explanation.
    pub message: String,
}

impl QeError {
    pub fn input(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            kind: ErrorKind::Input,
            code: code.into(),
            message: message.into(),
        }
    }

    pub fn state_conflict(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            kind: ErrorKind::StateConflict,
            code: code.into(),
            message: message.into(),
        }
    }

    pub fn resource(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            kind: ErrorKind::ResourceExhausted,
            code: code.into(),
            message: message.into(),
        }
    }

    pub fn computation(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            kind: ErrorKind::ComputationFailed,
            code: code.into(),
            message: message.into(),
        }
    }

    pub fn is_unknown_preserving(&self) -> bool {
        matches!(self.kind, ErrorKind::ResourceExhausted)
    }
}

impl std::fmt::Display for QeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "[{:?}/{}] {}", self.kind, self.code, self.message)
    }
}

impl std::error::Error for QeError {}

/// Convenience alias used by all fallible routines.
pub type QeResult<T> = Result<T, QeError>;
