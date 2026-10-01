//! Crate-wide error type. Unknown/error states are never silently reported as success:
//! every failure has a concrete [`ErrorKind`] and is surfaced (HTTP 4xx/5xx or Err).

use std::fmt;

/// Concrete failure categories. Tests assert on these, not just on "call failed".
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorKind {
    /// Operand tri-sets or indexes were built over differently-sized document universes.
    UniverseMismatch,
    /// Combined indexes were built with incompatible alive/deleted row sets.
    DeletesetMismatch,
    /// A bitmap had a structural byte-length problem.
    BitmapShape,
    /// Referenced column/table/index does not exist.
    NotFound,
    /// Value cannot be represented by / compared against the target column type.
    TypeMismatch,
    /// Request payload failed validation.
    InvalidInput,
    /// The predicate operator is unsupported for the given operand.
    UnsupportedOperator,
    /// Attempt to append rows to a table whose schema differs.
    SchemaMismatch,
}

#[derive(Debug)]
pub struct Error {
    pub kind: ErrorKind,
    pub message: String,
}

impl Error {
    pub fn new(kind: ErrorKind, message: impl Into<String>) -> Self {
        Self {
            kind,
            message: message.into(),
        }
    }

    pub fn invalid(message: impl Into<String>) -> Self {
        Self::new(ErrorKind::InvalidInput, message)
    }

    /// Stable machine-readable code used in API responses and test assertions.
    pub fn code(&self) -> &'static str {
        match self.kind {
            ErrorKind::UniverseMismatch => "UNIVERSE_MISMATCH",
            ErrorKind::DeletesetMismatch => "DELETESET_MISMATCH",
            ErrorKind::BitmapShape => "BITMAP_SHAPE",
            ErrorKind::NotFound => "NOT_FOUND",
            ErrorKind::TypeMismatch => "TYPE_MISMATCH",
            ErrorKind::InvalidInput => "INVALID_INPUT",
            ErrorKind::UnsupportedOperator => "UNSUPPORTED_OPERATOR",
            ErrorKind::SchemaMismatch => "SCHEMA_MISMATCH",
        }
    }

    pub fn http_status(&self) -> u16 {
        match self.kind {
            ErrorKind::NotFound => 404,
            ErrorKind::InvalidInput | ErrorKind::TypeMismatch | ErrorKind::UnsupportedOperator => {
                400
            }
            ErrorKind::UniverseMismatch
            | ErrorKind::DeletesetMismatch
            | ErrorKind::BitmapShape
            | ErrorKind::SchemaMismatch => 409,
        }
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.code(), self.message)
    }
}

impl std::error::Error for Error {}

pub type Result<T> = std::result::Result<T, Error>;
