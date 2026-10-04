//! Shared error taxonomy. Every variant maps to a stable, machine-readable
//! code that is surfaced in HTTP error bodies and audit records, so a caller
//! can tell *why* a submission was accepted, rejected, or left undetermined.

use thiserror::Error;

#[derive(Debug, Error, Clone, PartialEq, Eq)]
pub enum PsiError {
    #[error("point {index}: not a canonical ristretto255 encoding")]
    InvalidPointEncoding { index: usize },

    #[error("point {index}: the identity element is not allowed")]
    IdentityPoint { index: usize },

    #[error("point {index}: expected 32 bytes, got {got}")]
    BadPointLength { index: usize, got: usize },

    #[error("session not found")]
    SessionNotFound,

    #[error("invalid session state: expected {expected}, found {found}")]
    InvalidSessionState { expected: String, found: String },

    #[error("a_doubly has {got} entries but party A submitted {expected} points")]
    CountMismatch { expected: usize, got: usize },

    #[error("set size {got} exceeds limit {max}")]
    SetTooLarge { got: usize, max: usize },

    #[error("bad request: {0}")]
    BadRequest(String),

    #[error("internal error: {0}")]
    Internal(String),
}

impl PsiError {
    /// Stable machine-readable code for API consumers and audit records.
    pub fn code(&self) -> &'static str {
        match self {
            PsiError::InvalidPointEncoding { .. } => "INVALID_POINT_ENCODING",
            PsiError::IdentityPoint { .. } => "IDENTITY_POINT",
            PsiError::BadPointLength { .. } => "BAD_POINT_LENGTH",
            PsiError::SessionNotFound => "SESSION_NOT_FOUND",
            PsiError::InvalidSessionState { .. } => "INVALID_SESSION_STATE",
            PsiError::CountMismatch { .. } => "COUNT_MISMATCH",
            PsiError::SetTooLarge { .. } => "SET_TOO_LARGE",
            PsiError::BadRequest(_) => "BAD_REQUEST",
            PsiError::Internal(_) => "INTERNAL",
        }
    }

    /// HTTP status used by the server layer.
    pub fn http_status(&self) -> u16 {
        match self {
            PsiError::InvalidPointEncoding { .. }
            | PsiError::IdentityPoint { .. }
            | PsiError::BadPointLength { .. }
            | PsiError::CountMismatch { .. }
            | PsiError::SetTooLarge { .. }
            | PsiError::BadRequest(_) => 400,
            PsiError::SessionNotFound => 404,
            PsiError::InvalidSessionState { .. } => 409,
            PsiError::Internal(_) => 500,
        }
    }
}
