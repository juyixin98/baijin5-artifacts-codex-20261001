//! Crate-wide error type.
//!
//! The variant of every error is surfaced to API clients (see
//! [`crate::state::AppError`]) so that an unknown/exception state is never
//! silently reported as success.

use std::fmt;

/// All fallible operations in the crate return this error.
#[derive(Debug)]
pub enum TviError {
    /// The predicate referenced a column that is not in the table.
    UnknownColumn(String),
    /// The operator is not valid for the column's physical type.
    TypeMismatch {
        column: String,
        expected: &'static str,
        found: &'static str,
    },
    /// A query expression was malformed (bad arity, unknown operator, ...).
    InvalidQuery(String),
    /// Two bitmaps/relations were combined despite having different universes
    /// (document count or active row version).
    UniverseMismatch { expected: usize, found: usize },
    /// A literal could not be parsed into the column type.
    InvalidLiteral { column: String, value: String },
    /// Arrow2 could not decode a fixture column.
    Arrow(String),
    /// A fixture/data file could not be read or parsed.
    Io(String),
}

impl fmt::Display for TviError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            TviError::UnknownColumn(c) => write!(f, "unknown column: {c}"),
            TviError::TypeMismatch {
                column,
                expected,
                found,
            } => write!(
                f,
                "type mismatch on column `{column}`: expected {expected}, found {found}"
            ),
            TviError::InvalidQuery(msg) => write!(f, "invalid query: {msg}"),
            TviError::UniverseMismatch { expected, found } => write!(
                f,
                "universe mismatch: predicates/indexes must share the same document universe (expected {expected} rows, got {found})"
            ),
            TviError::InvalidLiteral { column, value } => {
                write!(f, "invalid literal for column `{column}`: {value:?}")
            }
            TviError::Arrow(msg) => write!(f, "arrow error: {msg}"),
            TviError::Io(msg) => write!(f, "io error: {msg}"),
        }
    }
}

impl std::error::Error for TviError {}

impl From<std::io::Error> for TviError {
    fn from(e: std::io::Error) -> Self {
        TviError::Io(e.to_string())
    }
}

impl From<arrow2::error::Error> for TviError {
    fn from(e: arrow2::error::Error) -> Self {
        TviError::Arrow(e.to_string())
    }
}

/// Crate result alias.
pub type Result<T> = std::result::Result<T, TviError>;
