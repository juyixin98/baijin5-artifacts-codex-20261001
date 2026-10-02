//! Categorized error types shared across the backend.
//!
//! Failures are never collapsed into "success": every error carries an explicit
//! [`FailureCategory`] that the API layer maps to an HTTP status and that tests
//! assert against concretely.

use std::fmt;

/// Coarse, machine-readable failure category.
///
/// `Incomplete*` values are *not* errors: the query ran correctly but hit a
/// declared bound. They are carried by the run status instead.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FailureCategory {
    /// The requested plan is structurally or semantically invalid.
    InvalidPlan,
    /// Input data violates the declared schema / constraints.
    InvalidData,
    /// A configured resource bound was exceeded (depth / rows / payload).
    ResourceLimit,
    /// An unexpected internal failure.
    Internal,
}

/// The single error type used by the crate.
#[derive(Debug, Clone)]
pub struct EngineError {
    pub category: FailureCategory,
    pub message: String,
}

impl EngineError {
    pub fn invalid_plan(message: impl Into<String>) -> Self {
        Self {
            category: FailureCategory::InvalidPlan,
            message: message.into(),
        }
    }

    pub fn invalid_data(message: impl Into<String>) -> Self {
        Self {
            category: FailureCategory::InvalidData,
            message: message.into(),
        }
    }

    pub fn resource_limit(message: impl Into<String>) -> Self {
        Self {
            category: FailureCategory::ResourceLimit,
            message: message.into(),
        }
    }

    pub fn internal(message: impl Into<String>) -> Self {
        Self {
            category: FailureCategory::Internal,
            message: message.into(),
        }
    }
}

impl fmt::Display for EngineError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:?}: {}", self.category, self.message)
    }
}

impl std::error::Error for EngineError {}

impl From<serde_json::Error> for EngineError {
    fn from(e: serde_json::Error) -> Self {
        EngineError::invalid_data(format!("malformed JSON payload: {e}"))
    }
}

/// Result alias used throughout the crate.
pub type EngineResult<T> = Result<T, EngineError>;
