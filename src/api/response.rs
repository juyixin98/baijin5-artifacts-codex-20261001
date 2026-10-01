//! JSON response models. Completion status is always explicit.

use serde::Serialize;

/// Trace entry mirrored from the run record.
#[derive(Clone, Debug, Serialize)]
pub struct TraceDto {
    /// Round number.
    pub round: u32,
    /// Depth expanded.
    pub depth: u32,
    /// Rows consumed.
    pub consumed: usize,
    /// Rows produced by the join.
    pub produced: usize,
    /// Cycle rows found.
    pub cycles: usize,
    /// Global-duplicate rows rejected.
    pub duplicates: usize,
    /// Rows admitted to the next working table.
    pub admitted: usize,
    /// Human-readable decision basis.
    pub basis: String,
}

/// A successful (possibly explicitly incomplete) execution response.
#[derive(Clone, Debug, Serialize)]
pub struct ExecuteResponse {
    /// Always `"ok"` here; malformed requests get an envelope with `"error"`.
    pub outcome: &'static str,
    /// Correlation id shared with server logs.
    pub run_id: String,
    /// Engine version.
    pub engine_version: String,
    /// CTE name.
    pub cte_name: String,
    /// `ALL` or `DISTINCT`.
    pub union_op: String,
    /// Traversal strategy used.
    pub order: String,
    /// `complete` | `incomplete_max_depth` | `incomplete_max_rows`.
    pub status: String,
    /// Whether the full fixpoint was reached.
    pub complete: bool,
    /// Output column names.
    pub columns: Vec<String>,
    /// Output rows as JSON scalars.
    pub rows: Vec<Vec<serde_json::Value>>,
    /// Number of rows.
    pub row_count: usize,
    /// Rows removed by global deduplication.
    pub rows_deduplicated: usize,
    /// Greatest emitted depth.
    pub max_depth_reached: u32,
    /// Limits in force.
    pub max_depth: u32,
    /// Limits in force.
    pub max_rows: u64,
    /// Number of rounds.
    pub rounds: u32,
    /// Round-by-round trace.
    pub trace: Vec<TraceDto>,
}

/// Error envelope. Validation failures are never reported as success.
#[derive(Clone, Debug, Serialize)]
pub struct ErrorResponse {
    /// Always `"error"`.
    pub outcome: &'static str,
    /// Machine-readable category: `validation_error` | `internal_error`.
    pub category: String,
    /// Human-readable detail.
    pub message: String,
    /// Run id when execution had started, else `null`.
    pub run_id: Option<String>,
}
