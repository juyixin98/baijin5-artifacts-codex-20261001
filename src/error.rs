//! Error taxonomy for the join service.
//!
//! Every failure falls into one of [`ErrorCode`] so callers can assert on a
//! stable category rather than matching prose. HTTP status mapping lives here
//! too so the transport layer never invents its own semantics.

use std::fmt;

/// Machine-stable failure categories.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorCode {
    /// Payload could not be parsed as JSON.
    MalformedJson,
    /// JSON was valid but violated the request schema (bad shape, bad types).
    InvalidRequest,
    /// A referenced relation or column was missing.
    UnknownRelation,
    /// Join columns were not type-compatible across relations.
    TypeMismatch,
    /// Natural join requested with zero common columns.
    DisjointSchema,
    /// A required join key carried NULL (SQL NULL-match policy).
    NullKey,
    /// No Leapfrog plan exists for this join shape (see [`crate::plan`]).
    UnsupportedShape,
    /// Requested page token is unknown or expired.
    InvalidCursor,
    /// Internal invariant violation; carries a redacted diagnostic id.
    Internal,
}

impl ErrorCode {
    /// Canonical short string used in JSON bodies.
    pub fn as_str(self) -> &'static str {
        match self {
            ErrorCode::MalformedJson => "malformed_json",
            ErrorCode::InvalidRequest => "invalid_request",
            ErrorCode::UnknownRelation => "unknown_relation",
            ErrorCode::TypeMismatch => "type_mismatch",
            ErrorCode::DisjointSchema => "disjoint_schema",
            ErrorCode::NullKey => "null_key",
            ErrorCode::UnsupportedShape => "unsupported_shape",
            ErrorCode::InvalidCursor => "invalid_cursor",
            ErrorCode::Internal => "internal_error",
        }
    }

    /// HTTP status implied by the category.
    pub fn http_status(self) -> u16 {
        match self {
            ErrorCode::MalformedJson
            | ErrorCode::InvalidRequest
            | ErrorCode::NullKey
            | ErrorCode::DisjointSchema
            | ErrorCode::InvalidCursor => 400,
            ErrorCode::UnknownRelation | ErrorCode::TypeMismatch | ErrorCode::UnsupportedShape => {
                422
            }
            ErrorCode::Internal => 500,
        }
    }
}

impl fmt::Display for ErrorCode {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Structured service error.
#[derive(Debug, Clone)]
pub struct ServiceError {
    pub code: ErrorCode,
    pub message: String,
    /// Optional JSON pointer / field path for precise diagnostics.
    pub field: Option<String>,
}

impl ServiceError {
    pub fn new(code: ErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
            field: None,
        }
    }

    /// Shorthand for [`ErrorCode::InvalidRequest`].
    pub fn request(message: impl Into<String>) -> Self {
        Self::new(ErrorCode::InvalidRequest, message)
    }

    pub fn with_field(mut self, field: impl Into<String>) -> Self {
        self.field = Some(field.into());
        self
    }
}

impl fmt::Display for ServiceError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match &self.field {
            Some(field) => write!(f, "{} [{}]: {}", self.code, field, self.message),
            None => write!(f, "{}: {}", self.code, self.message),
        }
    }
}

impl std::error::Error for ServiceError {}

/// Result alias used throughout the crate.
pub type Result<T> = std::result::Result<T, ServiceError>;
