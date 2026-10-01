//! Per-run record: identity, versioning, progress trace and final verdict.
//!
//! Every execution — success, incomplete, or failed validation upstream —
//! carries an explicit status. Unknown/exceptional conditions are never folded
//! into a success response.

use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::plan::UnionOp;

use super::limits::CompletionStatus;

/// Process-wide counter so two runs in the same nanosecond still differ.
static RUN_SEQUENCE: AtomicU64 = AtomicU64::new(0);

/// Engine version stamped into every run record.
pub const ENGINE_VERSION: &str = env!("CARGO_PKG_VERSION");

/// Generate a run identifier correlating logs to a specific invocation.
///
/// Format: `run-<unix millis>-<sequence>`. Local-only and dependency-free.
pub fn new_run_id() -> String {
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    let seq = RUN_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    format!("run-{millis}-{seq}")
}

/// One semi-naïve round (or stack step) in the execution trace.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TraceEntry {
    /// 0-based iteration/round number.
    pub round: u32,
    /// Traversal depth expanded in this round.
    pub depth: u32,
    /// Rows consumed from the working table.
    pub consumed: usize,
    /// Child rows produced by the join.
    pub produced: usize,
    /// Children that closed a path cycle.
    pub cycles: usize,
    /// Children rejected by global set dedup (`UNION DISTINCT`).
    pub duplicates: usize,
    /// Children admitted into the next working table.
    pub admitted: usize,
    /// Why the round produced its verdict (human-readable, deterministic).
    pub basis: String,
}

/// The full record of one execution.
#[derive(Clone, Debug)]
pub struct RunRecord {
    /// Correlation id shared by log lines and the JSON response.
    pub run_id: String,
    /// Engine version that produced the run.
    pub engine_version: String,
    /// Name of the CTE in the plan.
    pub cte_name: String,
    /// `UNION` flavour used.
    pub union_op: UnionOp,
    /// Final verdict — never implicitly "success".
    pub status: CompletionStatus,
    /// Number of expansion rounds executed.
    pub rounds: u32,
    /// Greatest depth emitted (seed rows are depth 0).
    pub max_depth_reached: u32,
    /// Rows in the final output (including cycle rows).
    pub rows_emitted: usize,
    /// Rows rejected as global duplicates.
    pub rows_deduplicated: usize,
    /// Limit that was in force during the run.
    pub max_depth: u32,
    /// Limit that was in force during the run.
    pub max_rows: u64,
    /// Round-by-round trace.
    pub trace: Vec<TraceEntry>,
}

impl RunRecord {
    /// Start a record for a new run.
    pub fn start(
        cte_name: impl Into<String>,
        union_op: UnionOp,
        max_depth: u32,
        max_rows: u64,
    ) -> Self {
        Self {
            run_id: new_run_id(),
            engine_version: ENGINE_VERSION.to_owned(),
            cte_name: cte_name.into(),
            union_op,
            status: CompletionStatus::Complete,
            rounds: 0,
            max_depth_reached: 0,
            rows_emitted: 0,
            rows_deduplicated: 0,
            max_depth,
            max_rows,
            trace: Vec::new(),
        }
    }
}
