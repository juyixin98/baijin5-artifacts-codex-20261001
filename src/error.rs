//! Error types for the mapping page model.
//!
//! Every failure surfaced by the model carries a stable [`ErrorCategory`] so
//! that tests and API clients can assert on the *kind* of failure (e.g. a
//! SIGBUS-analogue out-of-range access) instead of matching on message text.

use serde::Serialize;
use std::fmt;

/// Stable failure categories surfaced by the model and the HTTP API.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCategory {
    /// Access to a page lying wholly beyond EOF (SIGBUS analogue).
    AccessOutOfRange,
    /// Bad argument: unaligned offset, zero length, size limits, ...
    InvalidArgument,
    /// Unknown file or mapping.
    NotFound,
    /// Resource already exists (e.g. re-creating a file).
    Conflict,
    /// msync-equivalent failed; dirty marks of failed pages are retained.
    SyncFailed,
    /// Backing store failed outside of an explicit sync operation.
    StoreUnavailable,
}

/// Error returned by every model operation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ModelError {
    pub category: ErrorCategory,
    pub message: String,
    /// Pages whose writeback failed (only meaningful for `SyncFailed`).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub failed_pages: Vec<u64>,
}

impl ModelError {
    pub fn new(category: ErrorCategory, message: impl Into<String>) -> Self {
        ModelError {
            category,
            message: message.into(),
            failed_pages: Vec::new(),
        }
    }

    pub fn sync_failed(failed_pages: Vec<u64>, message: impl Into<String>) -> Self {
        ModelError {
            category: ErrorCategory::SyncFailed,
            message: message.into(),
            failed_pages,
        }
    }
}

impl fmt::Display for ModelError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:?}: {}", self.category, self.message)
    }
}

impl std::error::Error for ModelError {}
