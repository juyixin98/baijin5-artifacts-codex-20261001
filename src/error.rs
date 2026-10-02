//! Error contract shared by every operator and the validation entry point.
//!
//! Failures are deliberately split into disjoint [`ErrorKind`]s so that callers
//! (tests and the HTTP layer) can assert the *category* of a failure rather than
//! matching on a string. Two terminal-control categories are kept separate even
//! though they are both "not data":
//!
//! * [`ErrorKind::Timeout`]  — a wall-clock budget elapsed while polling a source.
//! * [`ErrorKind::Cancelled`] — the owner explicitly dropped the query token.
//!
//! Once an operator yields an error its stream is poisoned: every subsequent
//! [`crate::operator::Operator::next`] returns the same terminal error and never
//! touches the (possibly damaged) upstream again. See [`crate::operator::Operator`].

use std::fmt;

/// The class of a query failure. Stable, string-independent test surface.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ErrorKind {
    /// Malformed plan / batch / user argument detected at a boundary.
    InvalidInput,
    /// An operator method was called in the wrong lifecycle phase
    /// (e.g. `next` after `close`, double `finish`, pull after error).
    StateConflict,
    /// A memory or spill budget was exceeded, or a spill file could not be made.
    ResourceExhausted,
    /// Data-dependent computation failed (type mismatch in a join key, I/O on a
    /// valid-but-unreadable run, division-like failure inside a projection).
    ComputationFailed,
    /// Wall-clock deadline elapsed while waiting on a source.
    Timeout,
    /// The query was cancelled through its [`crate::cancel::CancellationToken`].
    Cancelled,
}

impl ErrorKind {
    /// Short, stable identifier used in JSON diagnostics and log replay.
    pub fn as_str(self) -> &'static str {
        match self {
            ErrorKind::InvalidInput => "invalid_input",
            ErrorKind::StateConflict => "state_conflict",
            ErrorKind::ResourceExhausted => "resource_exhausted",
            ErrorKind::ComputationFailed => "computation_failed",
            ErrorKind::Timeout => "timeout",
            ErrorKind::Cancelled => "cancelled",
        }
    }

    /// Whether this error is a *control* signal rather than a fault.
    pub fn is_control(self) -> bool {
        matches!(self, ErrorKind::Timeout | ErrorKind::Cancelled)
    }
}

impl fmt::Display for ErrorKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// The one error type used across the framework. Carries a stable [`ErrorKind`],
/// a human message, and structured [`Context`] for replay/diagnostics.
#[derive(Debug, Clone)]
pub struct QueryError {
    kind: ErrorKind,
    message: String,
    context: Context,
}

impl QueryError {
    pub fn new(kind: ErrorKind, message: impl Into<String>) -> Self {
        Self {
            kind,
            message: message.into(),
            context: Context::default(),
        }
    }

    pub fn invalid_input(m: impl Into<String>) -> Self {
        Self::new(ErrorKind::InvalidInput, m)
    }
    pub fn state_conflict(m: impl Into<String>) -> Self {
        Self::new(ErrorKind::StateConflict, m)
    }
    pub fn resource_exhausted(m: impl Into<String>) -> Self {
        Self::new(ErrorKind::ResourceExhausted, m)
    }
    pub fn computation(m: impl Into<String>) -> Self {
        Self::new(ErrorKind::ComputationFailed, m)
    }
    pub fn timeout(m: impl Into<String>) -> Self {
        Self::new(ErrorKind::Timeout, m)
    }
    pub fn cancelled(m: impl Into<String>) -> Self {
        Self::new(ErrorKind::Cancelled, m)
    }

    pub fn kind(&self) -> ErrorKind {
        self.kind
    }
    pub fn message(&self) -> &str {
        &self.message
    }
    pub fn context(&self) -> &Context {
        &self.context
    }

    /// Attach a structured diagnostic field, returning the error (builder style).
    pub fn with(mut self, key: &'static str, value: impl Into<String>) -> Self {
        self.context.push(key, value.into());
        self
    }

    /// Attach the name of the operator that observed the error.
    pub fn at(mut self, operator: impl Into<String>) -> Self {
        self.context.operator = Some(operator.into());
        self
    }
}

impl fmt::Display for QueryError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "[{}] {}", self.kind.as_str(), self.message)?;
        if let Some(op) = &self.context.operator {
            write!(f, " (operator={op})")?;
        }
        Ok(())
    }
}

impl std::error::Error for QueryError {}

/// Structured fields attached to an error for log replay.
#[derive(Debug, Clone, Default)]
pub struct Context {
    pub operator: Option<String>,
    pub fields: Vec<(&'static str, String)>,
}

impl Context {
    fn push(&mut self, key: &'static str, value: String) {
        self.fields.push((key, value));
    }
}

/// Framework result alias.
pub type QueryResult<T> = Result<T, QueryError>;

/// Convert an I/O error into a *computation* failure by default; spill call sites
/// that mean to signal budget exhaustion map explicitly.
impl From<std::io::Error> for QueryError {
    fn from(e: std::io::Error) -> Self {
        QueryError::computation(format!("io error: {e}"))
    }
}
