//! Engine error type.
//!
//! Errors fall into two broad families:
//! - [`EngineError::Validation`]: the request/plan itself is invalid and must
//!   be fixed by the caller. Execution never starts.
//! - [`EngineError::LimitExceeded`]: the plan was valid but resource limits
//!   stopped it; partial results plus an explicit *incomplete* status survive
//!   in the run record (handled by the executor, not this error).

use std::fmt;

/// Crate-wide result alias.
pub type Result<T> = std::result::Result<T, EngineError>;

/// All errors the engine can surface.
#[derive(Debug)]
pub enum EngineError {
    /// Invalid plan, schema, or input data.
    Validation(String),
    /// Internal invariant violated — a bug, never a user error.
    Internal(String),
}

impl EngineError {
    /// Construct a validation error.
    pub fn validation(msg: impl Into<String>) -> Self {
        EngineError::Validation(msg.into())
    }

    /// Construct an internal error.
    pub fn internal(msg: impl Into<String>) -> Self {
        EngineError::Internal(msg.into())
    }
}

impl fmt::Display for EngineError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            EngineError::Validation(m) => write!(f, "validation error: {m}"),
            EngineError::Internal(m) => write!(f, "internal error: {m}"),
        }
    }
}

impl std::error::Error for EngineError {}
