//! Typed errors with stable categories.
//!
//! Every failure the service can produce maps to one `ErrorCategory`, so
//! tests assert on *categories* (not message strings) and API clients get a
//! machine-readable reason.

use serde::Serialize;
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCategory {
    /// Chunker parameters are internally inconsistent.
    InvalidParams,
    /// A configured resource limit was exceeded (input bytes, chunk count, body size).
    LimitExceeded,
    /// Binary container failed structural parsing (magic, version, truncation, lengths).
    MalformedContainer,
    /// A chunk or payload digest did not match the recorded digest.
    DigestMismatch,
    /// Digests matched but recovered bytes differ from the supplied original —
    /// the digest layer is not trusted as a byte-equality proof.
    PayloadMismatch,
    /// Input could not be classified as accept or reject (e.g. undecodable header).
    Undetermined,
}

#[derive(Debug)]
pub struct CdcError {
    pub category: ErrorCategory,
    pub message: String,
}

impl CdcError {
    pub fn new(category: ErrorCategory, message: impl Into<String>) -> Self {
        Self {
            category,
            message: message.into(),
        }
    }
}

impl fmt::Display for CdcError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:?}: {}", self.category, self.message)
    }
}

impl std::error::Error for CdcError {}
