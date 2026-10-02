//! Wire-level plan types: the restricted `WITH RECURSIVE` request/response model.
//!
//! The supported subset is intentionally explicit (no free-form SQL):
//!
//! * a recursive view `R` with a typed column list, a seed (non-recursive term),
//! * exactly one recursive term of the form `SELECT project FROM R JOIN edges ON on`,
//! * `UNION` or `UNION ALL` between the two terms,
//! * optional path-aware cycle marking driven by a single *declared key column*.

use serde::{Deserialize, Serialize};

use crate::error::EngineResult;

/// Physical column types exposed on the wire.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ColumnType {
    /// 64-bit signed integer.
    Int64,
    /// UTF-8 string.
    Utf8,
    /// Boolean.
    Bool,
    /// List of 64-bit signed integers (used for integer-key paths).
    #[serde(rename = "list<int64>")]
    ListInt64,
    /// List of UTF-8 strings (used for text-key paths).
    #[serde(rename = "list<utf8>")]
    ListUtf8,
}

impl ColumnType {
    pub fn as_str(self) -> &'static str {
        match self {
            ColumnType::Int64 => "int64",
            ColumnType::Utf8 => "utf8",
            ColumnType::Bool => "bool",
            ColumnType::ListInt64 => "list<int64>",
            ColumnType::ListUtf8 => "list<utf8>",
        }
    }

    /// Scalar element type for list variants.
    pub fn list_element(self) -> Option<ColumnType> {
        match self {
            ColumnType::ListInt64 => Some(ColumnType::Int64),
            ColumnType::ListUtf8 => Some(ColumnType::Utf8),
            _ => None,
        }
    }
}

/// A declared column.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ColumnDecl {
    pub name: String,
    #[serde(rename = "type")]
    pub data_type: ColumnType,
}

/// A relation literal: typed columns plus rows of JSON values in column order.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Relation {
    pub columns: Vec<ColumnDecl>,
    /// Each row is a vector of JSON values in `columns` order.
    /// JSON `null` is allowed in non-key columns.
    pub rows: Vec<Vec<serde_json::Value>>,
}

/// One equi-join condition between the recursive view and the edge relation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JoinKey {
    /// Column of the recursive view.
    pub recursive: String,
    /// Column of the edge relation.
    pub edge: String,
}

/// Scalar/projection expressions evaluated per joined row.
///
/// Tagged enum on the `expr` field. Restricted to column references, literals
/// and integer arithmetic — enough for recursive walks (`depth + 1`) while
/// staying fully analyzable/validated.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "expr", rename_all = "snake_case")]
pub enum Expr {
    /// Reference to a column of the recursive view `R`.
    RecursiveColumn {
        name: String,
    },
    /// Reference to a column of the edge relation.
    EdgeColumn {
        name: String,
    },
    /// Typed literal (int64 / utf8 / bool / null).
    Literal {
        value: serde_json::Value,
    },
    Add {
        left: Box<Expr>,
        right: Box<Expr>,
    },
    Sub {
        left: Box<Expr>,
        right: Box<Expr>,
    },
}

/// One projection entry: value of output column `as` in the recursive term.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProjectionEntry {
    #[serde(rename = "as")]
    pub alias: String,
    #[serde(flatten)]
    pub value: Expr,
}

/// Declarative description of the recursive term.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RecursiveTerm {
    /// Name of the edge relation used on the right side of the join.
    pub edges_relation: String,
    /// Equi-join keys (conjunction). At least one required.
    pub on: Vec<JoinKey>,
    /// Projection for every *user-managed* output column; the engine appends
    /// the managed path/cycle columns itself.
    pub project: Vec<ProjectionEntry>,
}

/// How recursive cycles are handled.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CycleMode {
    /// Emit the revisiting row once with `cycle_column = true` and stop
    /// expanding that branch. Rows on the first visit carry `false`.
    Mark,
    /// Treat a revisit as a hard failure (`invalid_plan`/abort category).
    Error,
}

/// Path-aware cycle configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CycleConfig {
    pub mode: CycleMode,
    /// Name of the *single* declared key column of the recursive view.
    /// Cycle membership is computed on this value only — never on the whole
    /// row (which contains the ever-growing path and would never repeat).
    pub key_column: String,
    /// Name of the output list column carrying the walked key path.
    pub path_column: String,
    /// Name of the output boolean column carrying the cycle marker.
    pub cycle_column: String,
}

/// Set operator between the seed and recursive term.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SetQuantifier {
    /// Set semantics: output rows are deduplicated on the declared key
    /// (plus cycle marker); duplicates are suppressed globally.
    #[default]
    Union,
    /// Bag semantics: duplicates (e.g. repeated edges) are preserved.
    UnionAll,
}

/// Frontier traversal order.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TraversalOrder {
    /// Level-synchronous breadth-first expansion (classic semi-naïve rounds).
    #[default]
    Bfs,
    /// Depth-first stack expansion; same reachability semantics, different,
    /// still fully deterministic, output ordering.
    Dfs,
}

/// Declared safety bounds for one run.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunLimits {
    /// Maximum number of expansions along a single branch.
    #[serde(default = "default_max_depth")]
    pub max_depth: usize,
    /// Maximum number of emitted output rows (seed included).
    #[serde(default = "default_max_rows")]
    pub max_rows: usize,
}

fn default_max_depth() -> usize {
    64
}
fn default_max_rows() -> usize {
    10_000
}

impl Default for RunLimits {
    fn default() -> Self {
        Self {
            max_depth: default_max_depth(),
            max_rows: default_max_rows(),
        }
    }
}

/// The full recursive query request.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RecursiveRequest {
    /// Optional human label echoed into the run log.
    #[serde(default)]
    pub name: Option<String>,
    /// Columns of the recursive view (result schema).
    pub view: Relation,
    /// `union` (default) or `union_all`.
    #[serde(default)]
    pub set_quantifier: SetQuantifier,
    /// Base relations available to the recursive term (typically `edges`).
    pub relations: std::collections::BTreeMap<String, Relation>,
    pub recursive_term: RecursiveTerm,
    #[serde(default)]
    pub cycle: Option<CycleConfig>,
    #[serde(default)]
    pub limits: RunLimits,
    #[serde(default)]
    pub traversal_order: TraversalOrder,
    /// Attach the structured step-by-step run log to the response.
    #[serde(default = "default_true")]
    pub include_log: bool,
    /// Attach the base64 Arrow IPC stream of the result batch.
    #[serde(default = "default_true")]
    pub include_arrow_ipc: bool,
}

fn default_true() -> bool {
    true
}

/// Why a run stopped before fixpoint.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum IncompleteReason {
    MaxDepth,
    MaxRows,
}

/// Terminal verdict of a run. Exceptions are *never* reported as success.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RunStatus {
    /// Fixpoint reached (empty frontier) within all bounds.
    Complete,
    /// Stopped by a declared bound; the result is explicitly partial.
    Incomplete,
    /// Validation or runtime failure; no partial result is presented.
    Failed,
}

/// Counters describing the computation.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct RunStats {
    pub iterations: usize,
    pub seed_rows: usize,
    pub output_rows: usize,
    pub cycles_marked: usize,
    pub duplicates_suppressed: usize,
    pub join_probes: usize,
    pub elapsed_us: u128,
}

/// One structured log entry tied to the run identity.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunLogEntry {
    pub run_id: String,
    pub step: String,
    pub depth: Option<usize>,
    pub frontier_rows: Option<usize>,
    pub emitted: Option<usize>,
    pub cycles_marked: Option<usize>,
    pub duplicates_suppressed: Option<usize>,
    /// Human-readable decision basis for this step.
    pub detail: String,
}

/// The result envelope returned by the engine and the HTTP endpoint.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RecursiveResponse {
    pub run_id: String,
    pub engine_version: String,
    pub status: RunStatus,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub incomplete_reason: Option<IncompleteReason>,
    pub stats: RunStats,
    pub output: Relation,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub arrow_ipc_base64: Option<String>,
    #[serde(skip_serializing_if = "Vec::is_empty", default)]
    pub log: Vec<RunLogEntry>,
}

/// Error envelope used for `status = failed` responses.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ErrorResponse {
    pub run_id: String,
    pub engine_version: String,
    pub status: RunStatus,
    pub failure_category: crate::error::FailureCategory,
    pub message: String,
}

/// Validate cross-field invariants that serde cannot express.
pub fn validate_request(req: &RecursiveRequest) -> EngineResult<()> {
    if req.view.columns.is_empty() {
        return Err(crate::error::EngineError::invalid_plan(
            "recursive view must declare at least one column",
        ));
    }
    if req.view.rows.is_empty() {
        return Err(crate::error::EngineError::invalid_plan(
            "seed term must contain at least one row",
        ));
    }
    if req.limits.max_depth == 0 {
        return Err(crate::error::EngineError::invalid_plan(
            "limits.max_depth must be >= 1",
        ));
    }
    if req.limits.max_rows == 0 {
        return Err(crate::error::EngineError::invalid_plan(
            "limits.max_rows must be >= 1",
        ));
    }

    let view_col = |name: &str| -> EngineResult<&ColumnDecl> {
        req.view
            .columns
            .iter()
            .find(|c| c.name == name)
            .ok_or_else(|| {
                crate::error::EngineError::invalid_plan(format!(
                    "column '{name}' is not part of the recursive view"
                ))
            })
    };

    // Engine-managed columns (path/cycle) are neither provided by the seed
    // nor projected by the recursive term: the engine initializes and extends
    // them. Compute the managed set first so the seed arity check excludes it.
    let mut managed = std::collections::BTreeSet::new();
    if let Some(cyc) = &req.cycle {
        let key = view_col(&cyc.key_column)?;
        if !matches!(key.data_type, ColumnType::Int64 | ColumnType::Utf8) {
            return Err(crate::error::EngineError::invalid_plan(format!(
                "cycle.key_column '{}' must be int64 or utf8, got {}",
                cyc.key_column,
                key.data_type.as_str()
            )));
        }
        let path = view_col(&cyc.path_column)?;
        let expected_path = match key.data_type {
            ColumnType::Int64 => ColumnType::ListInt64,
            ColumnType::Utf8 => ColumnType::ListUtf8,
            other => unreachable!("checked above: {other:?}"),
        };
        if path.data_type != expected_path {
            return Err(crate::error::EngineError::invalid_plan(format!(
                "cycle.path_column '{}' must have type {}",
                cyc.path_column,
                expected_path.as_str()
            )));
        }
        let marker = view_col(&cyc.cycle_column)?;
        if marker.data_type != ColumnType::Bool {
            return Err(crate::error::EngineError::invalid_plan(format!(
                "cycle.cycle_column '{}' must be bool",
                cyc.cycle_column
            )));
        }
        if cyc.key_column == cyc.path_column
            || cyc.key_column == cyc.cycle_column
            || cyc.path_column == cyc.cycle_column
        {
            return Err(crate::error::EngineError::invalid_plan(
                "cycle key/path/cycle column names must be distinct",
            ));
        }
        for name in [&cyc.path_column, &cyc.cycle_column] {
            managed.insert(name.clone());
        }
    }

    // Seed rows arity: every non-managed column, in view order.
    let seed_arity = req
        .view
        .columns
        .iter()
        .filter(|c| !managed.contains(&c.name))
        .count();
    for (i, row) in req.view.rows.iter().enumerate() {
        if row.len() != seed_arity {
            return Err(crate::error::EngineError::invalid_data(format!(
                "seed row {i} has {} values, expected {seed_arity} (path/cycle columns are engine-managed)",
                row.len()
            )));
        }
    }

    // Edge relation and join keys.
    let edges = req
        .relations
        .get(&req.recursive_term.edges_relation)
        .ok_or_else(|| {
            crate::error::EngineError::invalid_plan(format!(
                "recursive_term.edges_relation '{}' is not declared in relations",
                req.recursive_term.edges_relation
            ))
        })?;
    if req.recursive_term.on.is_empty() {
        return Err(crate::error::EngineError::invalid_plan(
            "recursive_term.on must declare at least one equi-join key",
        ));
    }
    for k in &req.recursive_term.on {
        view_col(&k.recursive)?;
        if !edges.columns.iter().any(|c| c.name == k.edge) {
            return Err(crate::error::EngineError::invalid_plan(format!(
                "join edge column '{}' not found in relation '{}'",
                k.edge, req.recursive_term.edges_relation
            )));
        }
    }

    // Projection: every user-managed view column exactly once; managed
    // columns (path/cycle) must not appear in the user projection.
    let mut projected = std::collections::BTreeSet::new();
    for p in &req.recursive_term.project {
        if managed.contains(&p.alias) {
            return Err(crate::error::EngineError::invalid_plan(format!(
                "column '{}' is engine-managed by cycle config and must not appear in recursive_term.project",
                p.alias
            )));
        }
        view_col(&p.alias)?;
        if !projected.insert(p.alias.clone()) {
            return Err(crate::error::EngineError::invalid_plan(format!(
                "duplicate projection alias '{}'",
                p.alias
            )));
        }
        validate_expr_refs(&p.value, req, edges)?;
    }
    for c in &req.view.columns {
        if !managed.contains(&c.name) && !projected.contains(&c.name) {
            return Err(crate::error::EngineError::invalid_plan(format!(
                "recursive_term.project is missing output column '{}'",
                c.name
            )));
        }
    }

    // Relations arity + type validation happen when batches are built.
    Ok(())
}

fn validate_expr_refs(expr: &Expr, req: &RecursiveRequest, edges: &Relation) -> EngineResult<()> {
    match expr {
        Expr::RecursiveColumn { name } => {
            if !req.view.columns.iter().any(|c| &c.name == name) {
                return Err(crate::error::EngineError::invalid_plan(format!(
                    "expression references unknown recursive column '{name}'"
                )));
            }
        }
        Expr::EdgeColumn { name } => {
            if !edges.columns.iter().any(|c| &c.name == name) {
                return Err(crate::error::EngineError::invalid_plan(format!(
                    "expression references unknown edge column '{name}'"
                )));
            }
        }
        Expr::Literal { .. } => {}
        Expr::Add { left, right } | Expr::Sub { left, right } => {
            validate_expr_refs(left, req, edges)?;
            validate_expr_refs(right, req, edges)?;
        }
    }
    Ok(())
}
