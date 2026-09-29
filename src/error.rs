//! Error contract shared by every module.
//!
//! Failures are divided into four distinguishable categories required by the
//! specification. Each carries a machine-readable [`ErrorCode`] plus enough
//! context to be replayed (see [`crate::replay`]).

use thiserror::Error;

/// Machine-readable error codes, grouped by category in [`ErrorCategory`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorCode {
    // ---- Input validation ------------------------------------------------
    /// A batch is missing a column the plan references.
    MissingColumn,
    /// A column exists but its physical type does not match the declared type.
    TypeMismatch,
    /// A column length does not equal the batch row count.
    ColumnLengthMismatch,
    /// A predicate compares incompatible types (e.g. int against utf8).
    IncompatibleTypes,
    /// A predicate column is a type range joins do not support.
    UnsupportedType,
    /// The same column position is used inconsistently within one side.
    DuplicateBinding,
    /// A plan field is malformed (unknown operator, bad column index, ...).
    InvalidPlan,
    /// A request payload cannot be decoded.
    InvalidPayload,

    // ---- State conflicts -------------------------------------------------
    /// A paged-session id is unknown (expired or never created).
    UnknownSession,
    /// A cursor token does not correspond to the session it was presented with.
    CursorMismatch,
    /// A continuation was requested against a plan/session that is finished.
    SessionFinished,
    /// A run id in the replay log was not found (client-side lookup miss).
    UnknownRun,

    // ---- Resource exhaustion --------------------------------------------
    /// A configured row/result/candidate budget was exceeded.
    BudgetExceeded,
    /// The server-wide session registry reached its capacity.
    SessionLimitReached,

    // ---- Computation failure ---------------------------------------------
    /// An invariant of the join algorithm was violated (always a bug).
    InternalInvariant,
}

/// The four top-level failure categories. HTTP status mapping lives in the API
/// layer; the operator core never depends on HTTP.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorCategory {
    Input,
    State,
    Resource,
    Compute,
}

impl ErrorCode {
    pub fn category(self) -> ErrorCategory {
        match self {
            ErrorCode::MissingColumn
            | ErrorCode::TypeMismatch
            | ErrorCode::ColumnLengthMismatch
            | ErrorCode::IncompatibleTypes
            | ErrorCode::UnsupportedType
            | ErrorCode::DuplicateBinding
            | ErrorCode::InvalidPlan
            | ErrorCode::InvalidPayload => ErrorCategory::Input,
            ErrorCode::UnknownSession
            | ErrorCode::CursorMismatch
            | ErrorCode::SessionFinished
            | ErrorCode::UnknownRun => ErrorCategory::State,
            ErrorCode::BudgetExceeded | ErrorCode::SessionLimitReached => ErrorCategory::Resource,
            ErrorCode::InternalInvariant => ErrorCategory::Compute,
        }
    }

    /// Stable snake_case identifier used in JSON responses and logs.
    pub fn as_str(self) -> &'static str {
        match self {
            ErrorCode::MissingColumn => "missing_column",
            ErrorCode::TypeMismatch => "type_mismatch",
            ErrorCode::ColumnLengthMismatch => "column_length_mismatch",
            ErrorCode::IncompatibleTypes => "incompatible_types",
            ErrorCode::UnsupportedType => "unsupported_type",
            ErrorCode::DuplicateBinding => "duplicate_binding",
            ErrorCode::InvalidPlan => "invalid_plan",
            ErrorCode::InvalidPayload => "invalid_payload",
            ErrorCode::UnknownSession => "unknown_session",
            ErrorCode::UnknownRun => "unknown_run",
            ErrorCode::CursorMismatch => "cursor_mismatch",
            ErrorCode::SessionFinished => "session_finished",
            ErrorCode::BudgetExceeded => "budget_exceeded",
            ErrorCode::SessionLimitReached => "session_limit_reached",
            ErrorCode::InternalInvariant => "internal_invariant",
        }
    }
}

/// Crate-wide error type.
#[derive(Debug, Error)]
#[error("{code:?}: {message}")]
pub struct JoinError {
    pub code: ErrorCode,
    pub message: String,
}

impl JoinError {
    pub fn new(code: ErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }

    pub fn input(code: ErrorCode, message: impl Into<String>) -> Self {
        debug_assert_eq!(code.category(), ErrorCategory::Input);
        Self::new(code, message)
    }

    pub fn state(code: ErrorCode, message: impl Into<String>) -> Self {
        debug_assert_eq!(code.category(), ErrorCategory::State);
        Self::new(code, message)
    }

    pub fn resource(code: ErrorCode, message: impl Into<String>) -> Self {
        debug_assert_eq!(code.category(), ErrorCategory::Resource);
        Self::new(code, message)
    }

    pub fn compute(message: impl Into<String>) -> Self {
        Self::new(ErrorCode::InternalInvariant, message)
    }

    pub fn category(&self) -> ErrorCategory {
        self.code.category()
    }
}

/// Convenience alias used throughout the crate.
pub type JoinResult<T> = Result<T, JoinError>;
