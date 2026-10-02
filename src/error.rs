//! Failure categories shared by the merge engine, the API layer and tests.
//!
//! Every abnormal outcome is classified into one of these categories so that
//! tests can assert on *why* something failed, not just *that* it failed.

use serde::{Deserialize, Serialize};
use std::fmt;

/// Stable, machine-readable failure/uncertainty categories.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FailureCategory {
    /// A path (request path, layer entry, whiteout target) escapes the
    /// isolation root or is absolute where a relative path is required.
    PathEscapesRoot,
    /// Syntactically invalid path (empty, NUL byte, empty whiteout name, ...).
    InvalidPath,
    /// A symlink target resolves outside the merge root (or is absolute).
    SymlinkEscape,
    /// Hardlinks are outside the supported link scope.
    UnsupportedHardlink,
    /// File type outside scope (device, fifo, socket, ...).
    UnsupportedFileType,
    /// A referenced layer directory does not exist or is not a directory.
    LayerNotFound,
    /// Output directory exists and is not empty.
    OutputDirNotEmpty,
    /// Requested path is outside the configured workspace root.
    OutsideWorkspace,
    /// Run id not found in the store.
    RunNotFound,
    /// Catch-all for underlying I/O errors.
    Io,
}

impl fmt::Display for FailureCategory {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let s = serde_json::to_string(self).unwrap_or_else(|_| "\"unknown\"".into());
        write!(f, "{}", s.trim_matches('"'))
    }
}

/// Error type used at API/request-validation boundaries.
#[derive(Debug)]
pub struct MergeError {
    pub category: FailureCategory,
    pub message: String,
}

impl MergeError {
    pub fn new(category: FailureCategory, message: impl Into<String>) -> Self {
        Self {
            category,
            message: message.into(),
        }
    }
}

impl fmt::Display for MergeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.category, self.message)
    }
}

impl std::error::Error for MergeError {}

impl From<std::io::Error> for MergeError {
    fn from(e: std::io::Error) -> Self {
        MergeError::new(FailureCategory::Io, e.to_string())
    }
}
