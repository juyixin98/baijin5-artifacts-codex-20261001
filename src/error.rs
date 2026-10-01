//! Error taxonomy shared by validation, compilation and execution.
//!
//! Every failure has a stable machine-readable [`ErrorKind`] so that tests can
//! assert the *category* of failure rather than matching on prose.

use serde::Serialize;

/// Stable categories of failures. The string values are part of the API.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorKind {
    /// Request JSON / envelope malformed.
    InvalidRequest,
    /// A referenced relation or attribute does not exist.
    UnknownReference,
    /// Two operands have incompatible types.
    TypeMismatch,
    /// A literal cannot be coerced to the declared type.
    InvalidLiteral,
    /// The requested subquery shape is outside the supported fragment.
    UnsupportedForm,
    /// Correlation columns could not be aligned between inner and outer.
    CorrelationMismatch,
    /// A scalar subquery produced more than one row.
    ScalarMultipleRows,
    /// Aggregate is used where a non-aggregate value is required.
    MisplacedAggregate,
    /// A batch held data of a different physical type than its schema.
    MalformedBatch,
    /// Integer aggregation overflowed `int64`.
    NumericOverflow,
    /// Failure of the HTTP transport itself.
    Transport,
    /// Anything that should be impossible (internal invariant violated).
    Internal,
}

impl ErrorKind {
    pub fn as_str(self) -> &'static str {
        match self {
            ErrorKind::InvalidRequest => "invalid_request",
            ErrorKind::UnknownReference => "unknown_reference",
            ErrorKind::TypeMismatch => "type_mismatch",
            ErrorKind::InvalidLiteral => "invalid_literal",
            ErrorKind::UnsupportedForm => "unsupported_form",
            ErrorKind::CorrelationMismatch => "correlation_mismatch",
            ErrorKind::ScalarMultipleRows => "scalar_multiple_rows",
            ErrorKind::MisplacedAggregate => "misplaced_aggregate",
            ErrorKind::MalformedBatch => "malformed_batch",
            ErrorKind::NumericOverflow => "numeric_overflow",
            ErrorKind::Transport => "transport",
            ErrorKind::Internal => "internal",
        }
    }
}

impl std::fmt::Display for ErrorKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// The single error type used throughout the service.
#[derive(Debug, Clone, thiserror::Error)]
#[error("[{kind}] {message}")]
pub struct QError {
    pub kind: ErrorKind,
    pub message: String,
}

impl QError {
    pub fn new(kind: ErrorKind, message: impl Into<String>) -> Self {
        Self {
            kind,
            message: message.into(),
        }
    }

    pub fn invalid_request(msg: impl Into<String>) -> Self {
        Self::new(ErrorKind::InvalidRequest, msg)
    }
    pub fn unsupported(msg: impl Into<String>) -> Self {
        Self::new(ErrorKind::UnsupportedForm, msg)
    }
    pub fn unknown(msg: impl Into<String>) -> Self {
        Self::new(ErrorKind::UnknownReference, msg)
    }
    pub fn typemsg(msg: impl Into<String>) -> Self {
        Self::new(ErrorKind::TypeMismatch, msg)
    }
    pub fn internal(msg: impl Into<String>) -> Self {
        Self::new(ErrorKind::Internal, msg)
    }
}

pub type QResult<T> = Result<T, QError>;
