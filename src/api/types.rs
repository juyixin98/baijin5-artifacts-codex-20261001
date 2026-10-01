//! JSON request/response models for the HTTP validation boundary.
//!
//! Incoming JSON is deliberately *untyped* for row values (JSON scalars) and is
//! coerced against the declared column schema during validation.

use serde::{Deserialize, Serialize};

/// One `/execute` request.
#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExecuteRequest {
    /// CTE name (echoed in logs/response).
    #[serde(default = "default_cte_name")]
    pub cte_name: String,
    /// `UNION` flavour: `"ALL"` or `"DISTINCT"` (default).
    #[serde(default, rename = "union")]
    pub union_op: String,
    /// Traversal order: `bfs` (default), `dfs`, `input`.
    #[serde(default)]
    pub order: String,
    /// Business column definitions.
    pub schema: Vec<ColumnDef>,
    /// Declared cycle-key columns.
    pub key_columns: Vec<String>,
    /// Name of the appended rendered-path column.
    #[serde(default = "default_path_column")]
    pub path_column: String,
    /// Name of the appended cycle-flag column.
    #[serde(default = "default_cycle_column")]
    pub cycle_column: String,
    /// Base-term seed rows (positional JSON scalars matching `schema`).
    #[serde(default)]
    pub seed: Vec<Vec<serde_json::Value>>,
    /// Inline edge relation.
    pub edges: RelationDef,
    /// Recursive term definition.
    pub recursive: RecursiveTermDef,
    /// Per-run limits (optional; bounded by process ceilings).
    #[serde(default)]
    pub limits: Option<LimitsDef>,
}

fn default_cte_name() -> String {
    "recursive_cte".to_owned()
}
fn default_path_column() -> String {
    "path".to_owned()
}
fn default_cycle_column() -> String {
    "is_cycle".to_owned()
}

/// Column definition in a request.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ColumnDef {
    /// Column name.
    pub name: String,
    /// Column type: `int64 | utf8 | boolean`.
    #[serde(rename = "type")]
    pub data_type: String,
}

/// An inline relation (edge table).
#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RelationDef {
    /// Column definitions.
    pub schema: Vec<ColumnDef>,
    /// Rows as positional JSON scalars.
    #[serde(default)]
    pub rows: Vec<Vec<serde_json::Value>>,
}

/// Recursive term of the CTE.
#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RecursiveTermDef {
    /// Working-table column carrying the parent join key.
    pub parent_key: String,
    /// Edge-relation column carrying the source key.
    pub edge_from: String,
    /// Positional output bindings (one per CTE business column).
    pub projection: Vec<ProjectionDef>,
}

/// One projection binding.
#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProjectionDef {
    /// Take from an edge column by name.
    pub edge_column: Option<String>,
    /// Or use a JSON scalar literal.
    pub literal: Option<serde_json::Value>,
}

/// Per-request resource limits.
#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct LimitsDef {
    /// Maximum recursion depth (seed rows are depth 0).
    #[serde(default)]
    pub max_depth: Option<u32>,
    /// Maximum accumulated output rows.
    #[serde(default)]
    pub max_rows: Option<u64>,
}
