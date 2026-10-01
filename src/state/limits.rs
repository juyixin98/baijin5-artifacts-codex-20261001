//! Run resource limits and the outcome status of a single execution.

use crate::error::{EngineError, Result};

/// Effective limits for one run.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Limits {
    /// Maximum allowed depth (seed rows are depth 0).
    pub max_depth: u32,
    /// Maximum accumulated output rows.
    pub max_rows: u64,
}

impl Limits {
    /// Build limits, rejecting zero values.
    pub fn new(max_depth: u32, max_rows: u64) -> Result<Self> {
        if max_depth == 0 {
            return Err(EngineError::validation("max_depth must be >= 1"));
        }
        if max_rows == 0 {
            return Err(EngineError::validation("max_rows must be >= 1"));
        }
        Ok(Self {
            max_depth,
            max_rows,
        })
    }
}

/// Why an execution stopped.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CompletionStatus {
    /// Working table emptied naturally — the fixpoint was reached.
    Complete,
    /// The depth limit was hit; deeper rows were not expanded.
    IncompleteMaxDepth,
    /// The row limit was hit; further output was not collected.
    IncompleteMaxRows,
}

impl CompletionStatus {
    /// Stable machine-readable token.
    pub fn as_str(self) -> &'static str {
        match self {
            CompletionStatus::Complete => "complete",
            CompletionStatus::IncompleteMaxDepth => "incomplete_max_depth",
            CompletionStatus::IncompleteMaxRows => "incomplete_max_rows",
        }
    }

    /// Whether the result set covers the full fixpoint.
    pub fn is_complete(self) -> bool {
        matches!(self, CompletionStatus::Complete)
    }
}
