//! Wire-level request/response specification and validated query plan.
//!
//! The JSON shapes live here; [`crate::validate`] turns them into a
//! [`QueryPlan`] whose invariants are guaranteed by construction, so execution
//! never re-validates shapes.

use serde::{Deserialize, Serialize};

/// Supported logical column types.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum LogicalType {
    /// 64-bit signed integer
    I64,
    /// IEEE-754 double
    F64,
    /// UTF-8 string
    Utf8,
}

impl LogicalType {
    pub fn as_str(self) -> &'static str {
        match self {
            LogicalType::I64 => "i64",
            LogicalType::F64 => "f64",
            LogicalType::Utf8 => "utf8",
        }
    }
}

/// Columnar input column. JSON `null` marks a NULL cell.
#[derive(Debug, Clone, Deserialize)]
pub struct InputColumn {
    pub name: String,
    pub data_type: LogicalType,
    /// One JSON element per row; integers for i64, numbers for f64,
    /// strings/null for utf8.
    pub values: Vec<serde_json::Value>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum PercentileMethod {
    /// PERCENTILE_CONT: linear interpolation between order statistics
    Continuous,
    /// PERCENTILE_DISC: value at the ceil(p*N) rank
    Discrete,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SortOrder {
    Asc,
    Desc,
}

fn default_sort_asc() -> SortOrder {
    SortOrder::Asc
}

/// One aggregate operator applied to one measure column.
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum OperatorSpec {
    Percentile {
        column: String,
        /// Quantile in the closed interval `[0, 1]`; rejected otherwise.
        p: f64,
        method: PercentileMethod,
    },
    Mode {
        column: String,
    },
    StringAgg {
        column: String,
        #[serde(default = "default_delimiter")]
        delimiter: String,
        #[serde(default = "default_sort_asc")]
        order: SortOrder,
    },
}

fn default_delimiter() -> String {
    ",".to_string()
}

/// Local-only fault injection / resume controls. Synthetic fixtures use these
/// to exercise cancellation and resume; production callers leave them unset.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct ExecutionHints {
    /// Cancel cooperatively immediately after the run with this 1-based ordinal
    /// has been spilled (durable checkpoint). `None` disables injection.
    pub cancel_after_runs: Option<u32>,
    /// Per-query resident budget override (bytes, including string payloads).
    pub memory_budget_bytes: Option<usize>,
    /// Resume descriptor returned by an earlier cancelled response.
    pub resume: Option<ResumeToken>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ResumeToken {
    /// Spill directory name (under the configured spill root).
    pub spill_dir: String,
    /// Global row ordinal cursor: the next ingested row must use this ordinal.
    pub ordinal_cursor: u64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct QueryRequest {
    pub group_by: String,
    pub columns: Vec<InputColumn>,
    pub operators: Vec<OperatorSpec>,
    #[serde(default)]
    pub hints: ExecutionHints,
}

/// Typed group key after resolution.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum GroupKey {
    I64(i64),
    Utf8(String),
}

impl Serialize for GroupKey {
    fn serialize<S: serde::Serializer>(&self, s: S) -> std::result::Result<S::Ok, S::Error> {
        match self {
            GroupKey::I64(v) => s.serialize_i64(*v),
            GroupKey::Utf8(v) => s.serialize_str(v),
        }
    }
}

/// One operator bound to its column index and concrete type.
#[derive(Debug, Clone)]
pub struct BoundOperator {
    pub index: usize,
    pub spec: OperatorSpec,
    pub column_index: usize,
    pub column_type: LogicalType,
}

/// Validated plan: indices and types are resolved, `p` is in range.
#[derive(Debug)]
pub struct QueryPlan {
    pub group_column_index: usize,
    pub group_type: LogicalType,
    pub operators: Vec<BoundOperator>,
}

/// A result value for one (group, operator) pair.
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum OperatorResult {
    /// Computed value (may itself be SQL NULL via `value: null`).
    Ok {
        #[serde(rename = "type")]
        result_type: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        value: Option<serde_json::Value>,
        tie: bool,
        #[serde(skip_serializing_if = "Option::is_none")]
        frequency: Option<u64>,
        non_null: u64,
    },
    /// Input made the answer mathematically undecidable (e.g. NaN).
    Indeterminate { code: String, detail: String },
    /// Resource policy prevented completion (e.g. mode cardinality).
    Failed { code: String, detail: String },
}

#[derive(Debug, Clone, Serialize)]
pub struct GroupResult {
    pub group: Option<GroupKey>,
    pub rows: u64,
    pub results: Vec<OperatorResult>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Diagnostics {
    pub resident_budget_bytes: usize,
    pub resident_high_water_bytes: usize,
    pub groups_tracked: usize,
    pub groups_rejected_by_cap: u64,
    pub runs_spilled: u32,
    pub bytes_spilled: u64,
    /// True when this response describes a durable, resumable checkpoint.
    pub resumable: bool,
    pub resume: Option<ResumeToken>,
    pub ordinal_cursor: u64,
}

#[derive(Debug, Clone, Serialize)]
pub struct QueryResponse {
    pub request_id: String,
    pub status: &'static str,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub groups: Vec<GroupResult>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<ErrorBody>,
    pub diagnostics: Diagnostics,
}

#[derive(Debug, Clone, Serialize)]
pub struct ErrorBody {
    pub kind: String,
    pub code: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub field: Option<String>,
}
