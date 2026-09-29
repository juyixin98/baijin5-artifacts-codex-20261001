//! Resource limits and accounting.
//!
//! Budgets make "output over budget" explicit: when a result or candidate
//! ceiling is reached the operator stops at a deterministic boundary and
//! reports [`Outcome::Truncated`]. Stateful paging (see [`crate::state`])
//! resumes from that boundary instead of silently dropping suffix output.

use serde::{Deserialize, Serialize};

/// Runtime budgets for one execution (or one page of a paged execution).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Budget {
    /// Maximum pairs materialized per page/execution.
    pub max_output: usize,
    /// Maximum number of bitmap membership probes ("candidate accesses").
    /// This is the work metric tests assert on, not a wall-clock budget.
    pub max_candidate_accesses: u64,
}

impl Default for Budget {
    fn default() -> Self {
        Self {
            max_output: 1_000_000,
            max_candidate_accesses: u64::MAX,
        }
    }
}

impl Budget {
    pub fn new(max_output: usize, max_candidate_accesses: u64) -> Self {
        Self {
            max_output,
            max_candidate_accesses,
        }
    }
    pub fn unlimited() -> Self {
        Self::default()
    }
}

/// Whether an execution produced the complete join or stopped at a boundary.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Truncation {
    /// Every matching pair was emitted.
    Complete,
    /// Stopped because the output-pair ceiling was reached; more pairs exist.
    OutputLimit,
    /// Stopped because the candidate-access ceiling was reached; more pairs may
    /// exist.
    CandidateLimit,
}

/// Work accounting, surfaced in responses and replay logs.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct JoinStats {
    /// Number of emitted output pairs.
    pub emitted: usize,
    /// Number of bitmap membership probes performed while collecting pairs
    /// (the IEJoin "candidate access" count).
    pub candidate_accesses: u64,
    /// Number of outer driving rows processed before stopping.
    pub rows_driven: usize,
}

impl JoinStats {
    pub fn record_access(&mut self) {
        self.candidate_accesses += 1;
    }
}
