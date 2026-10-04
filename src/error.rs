//! Shared error taxonomy.
//!
//! Every module returns [`IbltError`]; the HTTP layer maps it to a status
//! code plus a machine-readable `category` so that callers can distinguish
//! input errors, state conflicts, resource exhaustion and computation
//! failures without parsing message strings.

use std::fmt;

/// Machine-readable error categories, serialized as snake_case strings.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCategory {
    /// Malformed JSON, bad base64, bad binary framing, invalid params,
    /// duplicate keys in one input set.
    InvalidInput,
    /// Two tables that cannot be combined (parameter mismatch).
    StateConflict,
    /// A configured limit was exceeded, or the table is too small to
    /// decode (the caller must re-encode with more cells).
    ResourceExhausted,
    /// An internal computation guard tripped (should not happen in
    /// normal operation).
    ComputationFailed,
}

/// The single error type crossing all module boundaries.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum IbltError {
    InvalidInput(String),
    StateConflict(String),
    ResourceExhausted(String),
    /// Decoding stalled before the table drained: some cells still hold
    /// unrecovered keys. This is never reported as a partial success;
    /// the only remedy is a larger table.
    DecodeIncomplete {
        remaining_nonzero_cells: usize,
        peeled: usize,
    },
    ComputationFailed(String),
}

impl IbltError {
    pub fn category(&self) -> ErrorCategory {
        match self {
            IbltError::InvalidInput(_) => ErrorCategory::InvalidInput,
            IbltError::StateConflict(_) => ErrorCategory::StateConflict,
            IbltError::ResourceExhausted(_) | IbltError::DecodeIncomplete { .. } => {
                ErrorCategory::ResourceExhausted
            }
            IbltError::ComputationFailed(_) => ErrorCategory::ComputationFailed,
        }
    }

    /// Stable machine-readable code, unique per variant.
    pub fn code(&self) -> &'static str {
        match self {
            IbltError::InvalidInput(_) => "invalid_input",
            IbltError::StateConflict(_) => "state_conflict",
            IbltError::ResourceExhausted(_) => "resource_exhausted",
            IbltError::DecodeIncomplete { .. } => "decode_incomplete",
            IbltError::ComputationFailed(_) => "computation_failed",
        }
    }

    pub fn message(&self) -> String {
        match self {
            IbltError::InvalidInput(m)
            | IbltError::StateConflict(m)
            | IbltError::ResourceExhausted(m)
            | IbltError::ComputationFailed(m) => m.clone(),
            IbltError::DecodeIncomplete {
                remaining_nonzero_cells,
                peeled,
            } => format!(
                "decode stalled: {remaining_nonzero_cells} cells still hold unrecovered keys \
                 after peeling {peeled} keys; re-encode with a larger table"
            ),
        }
    }
}

impl fmt::Display for IbltError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.code(), self.message())
    }
}

impl std::error::Error for IbltError {}
