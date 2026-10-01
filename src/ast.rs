//! Wire-level request/response DTOs.
//!
//! Requests are intentionally *not* SQL text: the service compiles a small,
//! explicit JSON plan surface. This keeps the "supported vs. deliberately
//! rejected" boundary visible and auditable. `deny_unknown_fields` means a
//! client cannot smuggle an unsupported construct through a typo'd field.

use serde::{Deserialize, Serialize};

// ---------- Catalog / fixtures ----------

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ColumnSpec {
    pub name: String,
    /// `INTEGER` | `TEXT` | `BOOLEAN`
    pub r#type: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RelationSpec {
    pub columns: Vec<ColumnSpec>,
    /// Rows are objects keyed by column name; a missing key or JSON null is NULL.
    pub rows: Vec<serde_json::Map<String, serde_json::Value>>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LoadFixturesRequest {
    /// Replace (true) or merge over (false) existing catalog relations.
    #[serde(default)]
    pub replace: bool,
    pub relations: std::collections::BTreeMap<String, RelationSpec>,
}

// ---------- Query ----------

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OuterSpec {
    pub relation: String,
    /// Columns to project from the outer relation. Empty/absent = all columns.
    #[serde(default)]
    pub select: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CorrelationSpec {
    pub outer: String,
    pub inner: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SubquerySpec {
    /// One of: `exists`, `not_exists`, `scalar`, `scalar_aggregate`.
    /// Recognized-but-rejected: `not_in`, `in`, `any`, `all`.
    pub op: String,
    pub inner_relation: String,
    pub correlation: Vec<CorrelationSpec>,
    /// `count_star` | `sum` (only for `scalar_aggregate`).
    pub aggregate: Option<String>,
    /// Inner column returned by `scalar`, or summed by `SUM`.
    pub value_column: Option<String>,
    /// Name of the subquery result column in the output.
    pub output_column: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct QueryRequest {
    /// Client-chosen correlation id; echoed back. A server id is generated when absent.
    pub query_id: Option<String>,
    pub outer: OuterSpec,
    pub subquery: SubquerySpec,
    /// Also run the independent row-by-row interpreter and include an equivalence verdict.
    /// Absent -> use the server configuration default (true).
    #[serde(default)]
    pub cross_check: Option<bool>,
}

// ---------- Responses ----------

#[derive(Debug, Clone, Serialize)]
pub struct ColumnOut {
    pub name: String,
    pub r#type: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct StepTrace {
    pub step: String,
    pub detail: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct RewriteProof {
    /// The algebraic identity the rewrite relies on.
    pub identity: String,
    /// Why it holds for the specific plan, including NULL/duplicate treatment.
    pub argument: Vec<String>,
    /// Semantic hazards that were explicitly checked (NOT IN, empty groups...).
    pub hazards_checked: Vec<String>,
    /// Conclusions the service does NOT claim (residual uncertainty).
    pub not_claimed: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ExecutorReport {
    pub mode: String,
    pub version: String,
    pub location: String,
    pub row_count: usize,
    /// Set only when this executor failed; the primary result is reported at top level.
    pub failure_category: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct EquivalenceVerdict {
    pub equivalent: bool,
    pub basis: String,
    pub detail: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct QuerySuccess {
    pub request_id: String,
    pub query_id: Option<String>,
    pub engine_version: String,
    pub columns: Vec<ColumnOut>,
    pub rows: Vec<serde_json::Value>,
    pub primary_executor: String,
    pub executors: Vec<ExecutorReport>,
    pub equivalence: Option<EquivalenceVerdict>,
    pub rewrite: RewriteProof,
    pub trace: Vec<StepTrace>,
    /// Uncertain conclusions are always reported separately from the result.
    pub uncertainties: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct QueryFailure {
    pub request_id: String,
    pub query_id: Option<String>,
    pub engine_version: String,
    pub failure: crate::error::Failure,
    /// Whether the independent row-by-row interpreter independently produced
    /// the *same* failure category. `null` when cross-check was not applicable.
    pub failure_cross_check: Option<bool>,
    pub trace: Vec<StepTrace>,
    /// Uncertain conclusions are always reported separately from hard failures.
    pub uncertainties: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct CatalogReport {
    pub request_id: String,
    pub loaded: Vec<String>,
    pub relations: Vec<String>,
}
