//! Error types with explicit failure categories.
//!
//! Every fallible entry point returns [`PctlError`] whose [`ErrorKind`] tells the
//! caller *why* a request was accepted, rejected, or could not be decided.

use std::fmt;
use std::io;

/// coarse failure category, surfaced verbatim in API responses.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorKind {
    /// request shape / parameter rejected before any execution state is created
    Validation,
    /// malformed request body that could not even be decoded
    InvalidRequest,
    /// a value made the result undecidable (e.g. NaN in a percentile measure)
    Indeterminate,
    /// memory/spill resource policy violation or disk I/O failure
    Resource,
    /// cooperatively cancelled; spill checkpoint is durable and resumable
    Cancelled,
}

impl ErrorKind {
    pub fn as_str(self) -> &'static str {
        match self {
            ErrorKind::Validation => "validation_rejected",
            ErrorKind::InvalidRequest => "invalid_request",
            ErrorKind::Indeterminate => "indeterminate",
            ErrorKind::Resource => "resource_error",
            ErrorKind::Cancelled => "cancelled",
        }
    }
}

#[derive(Debug)]
pub struct PctlError {
    pub kind: ErrorKind,
    pub code: String,
    pub message: String,
    /// JSON pointer-ish field path, e.g. `operators[2].p`
    pub field: Option<String>,
}

impl PctlError {
    pub fn new(kind: ErrorKind, code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            kind,
            code: code.into(),
            message: message.into(),
            field: None,
        }
    }

    pub fn at(mut self, field: impl Into<String>) -> Self {
        self.field = Some(field.into());
        self
    }

    pub fn validation(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self::new(ErrorKind::Validation, code, message)
    }
}

pub type Result<T> = std::result::Result<T, PctlError>;

impl fmt::Display for PctlError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match &self.field {
            Some(path) => write!(
                f,
                "{} [{}] at {}: {}",
                self.kind.as_str(),
                self.code,
                path,
                self.message
            ),
            None => write!(
                f,
                "{} [{}]: {}",
                self.kind.as_str(),
                self.code,
                self.message
            ),
        }
    }
}

impl std::error::Error for PctlError {}

impl From<io::Error> for PctlError {
    fn from(e: io::Error) -> Self {
        if e.kind() == io::ErrorKind::Interrupted {
            PctlError::new(ErrorKind::Resource, "io_interrupted", e.to_string())
        } else {
            PctlError::new(ErrorKind::Resource, "io_error", e.to_string())
        }
    }
}

impl From<serde_json::Error> for PctlError {
    fn from(e: serde_json::Error) -> Self {
        PctlError::new(
            ErrorKind::InvalidRequest,
            "malformed_json",
            format!("request body is not valid JSON: {e}"),
        )
    }
}
