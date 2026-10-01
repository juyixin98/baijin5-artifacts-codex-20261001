//! Structured error types shared across every crate.
//!
//! Failures are categorized (see [`ErrorCategory`]) so that the API surface and
//! the test suite can assert *which* failure happened instead of only asserting
//! that something failed.

use std::fmt;

// Re-exported so callers can write `sl_types::error::Result`.
use serde::{Deserialize, Serialize};

/// Crate-wide result alias.
pub type Result<T> = std::result::Result<T, SlError>;

/// Coarse failure classes. The categories are part of the public contract of
/// the validation entry point and of the operator; tests match on them.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCategory {
    /// Malformed request: unknown column, bad types, duplicated keys, ...
    Validation,
    /// `OFFSET + LIMIT` (or `OFFSET` alone against `u64::MAX`) would overflow.
    Overflow,
    /// A configured in-memory state budget was exceeded under the `reject` policy.
    BudgetExceeded,
    /// External (spill) storage failed or the combination of options is not
    /// serviceable with the configured resource policy.
    ExternalStorage,
    /// The upstream batch source returned an error.
    Source,
    /// An invariant of the engine itself was violated (bug, not bad input).
    Internal,
}

/// Structured error record. `location` is the module/phase that produced it,
/// kept stable for log correlation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ErrorRecord {
    pub category: ErrorCategory,
    /// Stable machine-readable code, e.g. `offset_limit_overflow`.
    pub code: String,
    /// Human-readable explanation (safe to surface; no secrets in this engine).
    pub message: String,
    /// Module / processing phase, e.g. `sl_validate::entry`.
    pub location: String,
}

impl ErrorRecord {
    pub fn new(
        category: ErrorCategory,
        code: impl Into<String>,
        message: impl Into<String>,
        location: impl Into<String>,
    ) -> Self {
        Self {
            category,
            code: code.into(),
            message: message.into(),
            location: location.into(),
        }
    }
}

/// The one error type used by all first-party crates.
#[derive(Debug, Clone)]
pub struct SlError {
    pub record: ErrorRecord,
}

impl SlError {
    pub fn new(
        category: ErrorCategory,
        code: impl Into<String>,
        message: impl Into<String>,
        location: impl Into<String>,
    ) -> Self {
        Self {
            record: ErrorRecord::new(category, code, message, location),
        }
    }

    pub fn validation(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self::new(
            ErrorCategory::Validation,
            code,
            message,
            "sl_types::error",
        )
    }

    pub fn internal(message: impl Into<String>) -> Self {
        Self::new(
            ErrorCategory::Internal,
            "internal_invariant",
            message,
            "sl_types::error",
        )
    }

    pub fn category(&self) -> ErrorCategory {
        self.record.category
    }
}

impl fmt::Display for SlError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "[{:?}/{}] {} (at {})",
            self.record.category,
            self.record.code,
            self.record.message,
            self.record.location
        )
    }
}

impl std::error::Error for SlError {}

/// Convert an arrow2 error into our categorized error.
impl From<arrow2::error::Error> for SlError {
    fn from(e: arrow2::error::Error) -> Self {
        SlError::new(
            ErrorCategory::Internal,
            "arrow2",
            format!("arrow2 failure: {e}"),
            "sl_types::arrow",
        )
    }
}

impl From<std::io::Error> for SlError {
    fn from(e: std::io::Error) -> Self {
        SlError::new(
            ErrorCategory::ExternalStorage,
            "io",
            format!("I/O failure: {e}"),
            "sl_resource::spill",
        )
    }
}
