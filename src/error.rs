//! Crate-wide error model with explicit, testable failure categories.

use std::fmt;

/// Every fallible entry point in the crate maps to one of these categories so
/// that tests can assert the *kind* of failure rather than matching on text.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorKind {
    /// Quantile parameter outside the closed `[0, 1]` range — rejected before
    /// any operator executes.
    InvalidQuantile,
    /// Request/plan shape problem: unknown column, type mismatch, empty input
    /// schema, etc.
    InvalidRequest,
    /// A value could not be parsed into the declared column type.
    ParseError,
    /// The configured memory budget was exceeded in a way that could not be
    /// spilled (or spilling failed).
    BudgetExceeded,
    /// Cooperative cancellation was observed (or a query id was cancelled).
    Cancelled,
    /// A resumption token was malformed, referenced missing spill files, or was
    /// replayed with a different plan.
    InvalidResume,
    /// Something went wrong in the spill directory (create/read/write).
    SpillIo,
    /// A feature combination that is not supported.
    Unsupported,
}

impl ErrorKind {
    pub fn as_str(self) -> &'static str {
        match self {
            ErrorKind::InvalidQuantile => "INVALID_QUANTILE",
            ErrorKind::InvalidRequest => "INVALID_REQUEST",
            ErrorKind::ParseError => "PARSE_ERROR",
            ErrorKind::BudgetExceeded => "BUDGET_EXCEEDED",
            ErrorKind::Cancelled => "CANCELLED",
            ErrorKind::InvalidResume => "INVALID_RESUME",
            ErrorKind::SpillIo => "SPILL_IO",
            ErrorKind::Unsupported => "UNSUPPORTED",
        }
    }

    pub fn http_status(self) -> u16 {
        match self {
            ErrorKind::InvalidQuantile | ErrorKind::InvalidRequest | ErrorKind::ParseError => 400,
            ErrorKind::InvalidResume => 409,
            ErrorKind::BudgetExceeded | ErrorKind::SpillIo | ErrorKind::Unsupported => 507,
            ErrorKind::Cancelled => 499,
        }
    }
}

impl fmt::Display for ErrorKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
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

    pub fn invalid_quantile(q: f64) -> Self {
        Self::new(
            ErrorKind::InvalidQuantile,
            format!("quantile {q} is out of the closed range [0.0, 1.0]"),
        )
    }

    pub fn invalid_request(msg: impl Into<String>) -> Self {
        Self::new(ErrorKind::InvalidRequest, msg)
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.kind, self.message)
    }
}

impl std::error::Error for Error {}

pub type Result<T> = std::result::Result<T, Error>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn categories_round_trip() {
        assert_eq!(ErrorKind::InvalidQuantile.as_str(), "INVALID_QUANTILE");
        assert_eq!(ErrorKind::InvalidQuantile.http_status(), 400);
        assert_eq!(ErrorKind::Cancelled.http_status(), 499);
        assert_eq!(ErrorKind::BudgetExceeded.http_status(), 507);
    }

    #[test]
    fn out_of_range_quantile_is_invalid_quantile() {
        let err = Error::invalid_quantile(1.5);
        assert_eq!(err.kind, ErrorKind::InvalidQuantile);
        assert!(err.to_string().contains("1.5"));
    }
}
