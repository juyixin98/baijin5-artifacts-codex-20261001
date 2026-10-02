//! Error contract.
//!
//! Every fallible boundary in the crate returns [`SetOpError`]. The four
//! [`ErrorKind`] variants are *distinguishable by construction* and map to
//! distinct HTTP status codes and distinct `kind` strings in the JSON body, so
//! callers (and the replay logs) can tell apart:
//!
//! - a bad request (`input`),
//! - a conflicting run lifecycle transition (`state_conflict`),
//! - an out-of-memory / out-of-disk / overflow budget (`resource_exhausted`),
//! - and an internal computation failure (`computation_failed`).

use std::fmt;

use serde::Serialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorKind {
    /// Malformed or inconsistent input supplied by the caller.
    Input,
    /// A run/state machine transition that is not legal for the current state.
    StateConflict,
    /// A configured resource budget (memory, spill bytes, spill files) was hit,
    /// or row multiplicity overflowed the 64-bit counter.
    ResourceExhausted,
    /// The engine failed while processing otherwise-valid input.
    ComputationFailed,
}

impl ErrorKind {
    pub fn as_str(self) -> &'static str {
        match self {
            ErrorKind::Input => "input",
            ErrorKind::StateConflict => "state_conflict",
            ErrorKind::ResourceExhausted => "resource_exhausted",
            ErrorKind::ComputationFailed => "computation_failed",
        }
    }

    pub fn http_status(self) -> u16 {
        match self {
            ErrorKind::Input => 400,
            ErrorKind::StateConflict => 409,
            ErrorKind::ResourceExhausted => 507, // Insufficient Storage
            ErrorKind::ComputationFailed => 500,
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct SetOpError {
    pub kind: ErrorKind,
    /// Stable machine-readable code, e.g. `schema_mismatch`, `count_overflow`.
    pub code: String,
    pub message: String,
    /// Run the error belongs to, when one is in scope.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub run_id: Option<String>,
}

impl SetOpError {
    pub fn new(kind: ErrorKind, code: &str, message: impl Into<String>) -> Self {
        Self {
            kind,
            code: code.to_string(),
            message: message.into(),
            run_id: None,
        }
    }

    pub fn input(code: &str, message: impl Into<String>) -> Self {
        Self::new(ErrorKind::Input, code, message)
    }
    pub fn state(code: &str, message: impl Into<String>) -> Self {
        Self::new(ErrorKind::StateConflict, code, message)
    }
    pub fn resource(code: &str, message: impl Into<String>) -> Self {
        Self::new(ErrorKind::ResourceExhausted, code, message)
    }
    pub fn compute(code: &str, message: impl Into<String>) -> Self {
        Self::new(ErrorKind::ComputationFailed, code, message)
    }

    pub fn with_run(mut self, run_id: impl Into<String>) -> Self {
        self.run_id = Some(run_id.into());
        self
    }
}

impl fmt::Display for SetOpError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match &self.run_id {
            Some(r) => write!(
                f,
                "[{}/{}] run={}: {}",
                self.kind.as_str(),
                self.code,
                r,
                self.message
            ),
            None => write!(f, "[{}/{}] {}", self.kind.as_str(), self.code, self.message),
        }
    }
}

impl std::error::Error for SetOpError {}

pub type Result<T> = std::result::Result<T, SetOpError>;

/// Re-export of the [`std::result::Result`] alias used across the crate.
#[macro_export]
macro_rules! bail_input {
    ($code:expr, $($t:tt)*) => { return Err($crate::error::SetOpError::input($code, format!($($t)*))) };
}
