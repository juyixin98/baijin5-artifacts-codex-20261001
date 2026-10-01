//! Error model. Every engine failure has a stable category/code so that API
//! clients (and the independent test suite) can assert the *class* of a
//! failure, not just match on a human-readable message.

use serde::Serialize;

/// Stable failure categories used in API responses and logs.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FailureCategory {
    /// The request uses a SQL form this service deliberately does not compile
    /// (e.g. `NOT IN`, whose NULL semantics differ from `NOT EXISTS`).
    UnsupportedForm,
    /// Request shape is valid JSON but not a legal query for this service.
    Validation,
    /// A referenced relation or column does not exist, or types do not line up.
    Schema,
    /// A scalar subquery returned more than one row for an outer row.
    CardinalityViolation,
    /// Anything that should never happen; treated as an uncertain conclusion.
    Execution,
}

impl FailureCategory {
    pub fn code(&self) -> &'static str {
        match self {
            FailureCategory::UnsupportedForm => "unsupported_form",
            FailureCategory::Validation => "validation_error",
            FailureCategory::Schema => "schema_error",
            FailureCategory::CardinalityViolation => "scalar_cardinality_violation",
            FailureCategory::Execution => "execution_error",
        }
    }

    pub fn label(&self) -> &'static str {
        match self {
            FailureCategory::UnsupportedForm => "unsupported form",
            FailureCategory::Validation => "validation error",
            FailureCategory::Schema => "schema error",
            FailureCategory::CardinalityViolation => "scalar cardinality violation",
            FailureCategory::Execution => "execution error",
        }
    }
}

/// The single error type used across validation, optimization and execution.
#[derive(Debug, thiserror::Error)]
pub enum EngineError {
    #[error("unsupported SQL form `{form}` at {location}: {reason}")]
    Unsupported {
        form: String,
        location: String,
        reason: String,
        remediation: String,
    },

    #[error("validation error at {location}: {message}")]
    Validation { message: String, location: String },

    #[error("schema error: {message}")]
    Schema { message: String },

    #[error(
        "scalar subquery returned {rows} rows for outer row {outer_row} (0-based); at most 1 row is allowed"
    )]
    ScalarCardinality { outer_row: usize, rows: usize },

    #[error("execution error: {message}")]
    Execution { message: String },
}

impl EngineError {
    pub fn schema(msg: impl Into<String>) -> Self {
        EngineError::Schema {
            message: msg.into(),
        }
    }

    pub fn validation(msg: impl Into<String>, location: impl Into<String>) -> Self {
        EngineError::Validation {
            message: msg.into(),
            location: location.into(),
        }
    }

    pub fn execution(msg: impl Into<String>) -> Self {
        EngineError::Execution {
            message: msg.into(),
        }
    }

    pub fn category(&self) -> FailureCategory {
        match self {
            EngineError::Unsupported { .. } => FailureCategory::UnsupportedForm,
            EngineError::Validation { .. } => FailureCategory::Validation,
            EngineError::Schema { .. } => FailureCategory::Schema,
            EngineError::ScalarCardinality { .. } => FailureCategory::CardinalityViolation,
            EngineError::Execution { .. } => FailureCategory::Execution,
        }
    }

    /// Serializable projection of an error, rendered as the API `failure`
    /// object (kept separate from ordinary result payloads on purpose).
    pub fn to_failure(&self) -> Failure {
        let category = self.category();
        match self {
            EngineError::Unsupported { form, location, reason, remediation } => Failure {
                category: category.code(),
                message: self.to_string(),
                location: Some(location.clone()),
                unsupported_form: Some(form.clone()),
                reason: Some(reason.clone()),
                remediation: Some(remediation.clone()),
            },
            EngineError::Validation { message, location } => Failure {
                category: category.code(),
                message: message.clone(),
                location: Some(location.clone()),
                unsupported_form: None,
                reason: None,
                remediation: Some("correct the request and resubmit".to_string()),
            },
            EngineError::Schema { message } => Failure {
                category: category.code(),
                message: message.clone(),
                location: None,
                unsupported_form: None,
                reason: None,
                remediation: Some("check relation and column references".to_string()),
            },
            EngineError::ScalarCardinality { outer_row, rows } => Failure {
                category: category.code(),
                message: self.to_string(),
                location: Some(format!("outer_row[{outer_row}]")),
                unsupported_form: None,
                reason: Some(format!(
                    "a scalar subquery must yield exactly one row per outer row; {rows} rows matched"
                )),
                remediation: Some(
                    "add an aggregate (COUNT/SUM), a correlation key, or refine the predicate"
                        .to_string(),
                ),
            },
            EngineError::Execution { message } => Failure {
                category: category.code(),
                message: message.clone(),
                location: None,
                unsupported_form: None,
                reason: Some("internal execution failure; result equivalence is not guaranteed".to_string()),
                remediation: Some("report this failure with the request_id and trace".to_string()),
            },
        }
    }
}

pub type EngineResult<T> = Result<T, EngineError>;

/// A failure as it appears in API responses.
#[derive(Debug, Clone, Serialize)]
pub struct Failure {
    pub category: &'static str,
    pub message: String,
    pub location: Option<String>,
    pub unsupported_form: Option<String>,
    pub reason: Option<String>,
    pub remediation: Option<String>,
}
