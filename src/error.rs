//! Error contract shared by the operator, engine, API and CLI.
//!
//! Every fallible path in the project reports a [`JoinError`] whose
//! [`ErrorCategory`] is one of the four values that callers are required to
//! distinguish:
//!
//! | category             | meaning                                  | HTTP |
//! |----------------------|------------------------------------------|------|
//! | `Input`              | malformed request / schema / plan        | 400  |
//! | `StateConflict`      | resume cursor, session or replay state   | 409  |
//! | `ResourceExhausted`  | output / memory / session budget         | 413  |
//! | `Compute`            | internal invariant or replay divergence  | 500  |

use std::fmt;

use serde::Serialize;

/// Coarse, externally visible failure class.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCategory {
    /// Caller-supplied data/plan is invalid.
    Input,
    /// A stateful interaction conflicts with stored state.
    StateConflict,
    /// A configured budget would be exceeded.
    ResourceExhausted,
    /// An internal invariant (or replay reproduction) failed.
    Compute,
}

impl ErrorCategory {
    /// HTTP status used by the Axum layer.
    #[must_use]
    pub fn http_status(self) -> u16 {
        match self {
            ErrorCategory::Input => 400,
            ErrorCategory::StateConflict => 409,
            ErrorCategory::ResourceExhausted => 413,
            ErrorCategory::Compute => 500,
        }
    }
}

impl fmt::Display for ErrorCategory {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let s = match self {
            ErrorCategory::Input => "input",
            ErrorCategory::StateConflict => "state_conflict",
            ErrorCategory::ResourceExhausted => "resource_exhausted",
            ErrorCategory::Compute => "compute",
        };
        f.write_str(s)
    }
}

/// Structured error. `code` is a stable machine-readable identifier;
/// `details` carries structured context (limits, positions, run id, ...).
#[derive(Debug, Serialize)]
pub struct JoinError {
    pub category: ErrorCategory,
    pub code: &'static str,
    pub message: String,
    #[serde(skip_serializing_if = "serde_json::Map::is_empty")]
    pub details: serde_json::Map<String, serde_json::Value>,
}

impl JoinError {
    fn new(category: ErrorCategory, code: &'static str, message: impl Into<String>) -> Self {
        Self {
            category,
            code,
            message: message.into(),
            details: serde_json::Map::new(),
        }
    }

    pub fn input(code: &'static str, message: impl Into<String>) -> Self {
        Self::new(ErrorCategory::Input, code, message)
    }

    pub fn conflict(code: &'static str, message: impl Into<String>) -> Self {
        Self::new(ErrorCategory::StateConflict, code, message)
    }

    pub fn exhausted(code: &'static str, message: impl Into<String>) -> Self {
        Self::new(ErrorCategory::ResourceExhausted, code, message)
    }

    pub fn compute(code: &'static str, message: impl Into<String>) -> Self {
        Self::new(ErrorCategory::Compute, code, message)
    }

    /// Attach one structured detail field; builder style.
    #[must_use]
    pub fn with(mut self, key: &str, value: serde_json::Value) -> Self {
        self.details.insert(key.to_owned(), value);
        self
    }

    /// Attach the run id so every failure can be correlated with a trace.
    #[must_use]
    pub fn with_run(mut self, run_id: &str) -> Self {
        self.details.insert(
            "run_id".to_owned(),
            serde_json::Value::String(run_id.to_owned()),
        );
        self
    }
}

impl fmt::Display for JoinError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "[{}/{}] {}", self.category, self.code, self.message)
    }
}

impl std::error::Error for JoinError {}

pub type JoinResult<T> = Result<T, JoinError>;
