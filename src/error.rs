//! Error taxonomy. Every failure carries a stable *category* so tests and
//! operators can assert on the failure class, not on message text.

use std::fmt;

use crate::diag::Decision;

/// Stable failure categories (used in diagnostics and tests).
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCategory {
    /// Page absent from cache and absent from the backing store.
    NotFound,
    /// Backing store I/O failed; the engine cannot determine the outcome.
    Store,
    /// A dirty victim could not be written back; the access is rejected and
    /// the cache state is left untouched.
    Writeback,
    /// Malformed request (e.g. write without payload).
    BadRequest,
    /// Snapshot save/restore problem.
    Snapshot,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ArcError {
    PageNotFound { page: u64 },
    Store { page: u64, message: String },
    Writeback { page: u64, message: String },
    BadRequest { message: String },
    Snapshot { message: String },
}

impl ArcError {
    pub fn category(&self) -> ErrorCategory {
        match self {
            ArcError::PageNotFound { .. } => ErrorCategory::NotFound,
            ArcError::Store { .. } => ErrorCategory::Store,
            ArcError::Writeback { .. } => ErrorCategory::Writeback,
            ArcError::BadRequest { .. } => ErrorCategory::BadRequest,
            ArcError::Snapshot { .. } => ErrorCategory::Snapshot,
        }
    }

    /// Map the error onto a diagnostic decision: rejections are definitive,
    /// store I/O failures leave the outcome undecidable.
    pub fn decision(&self) -> Decision {
        match self {
            ArcError::Store { message, .. } => Decision::Undecidable {
                reason: format!("backing store I/O failure: {message}"),
            },
            other => Decision::Rejected {
                category: other.category(),
                reason: other.to_string(),
            },
        }
    }
}

impl fmt::Display for ArcError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ArcError::PageNotFound { page } => write!(f, "page {page} not found in backing store"),
            ArcError::Store { page, message } => write!(f, "store error on page {page}: {message}"),
            ArcError::Writeback { page, message } => {
                write!(f, "write-back of dirty page {page} failed: {message}")
            }
            ArcError::BadRequest { message } => write!(f, "bad request: {message}"),
            ArcError::Snapshot { message } => write!(f, "snapshot error: {message}"),
        }
    }
}

impl std::error::Error for ArcError {}
