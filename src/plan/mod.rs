//! Query operators for the supported recursive-CTE subset.
//!
//! The grammar implemented here is exactly:
//!
//! ```sql
//! WITH RECURSIVE <name> AS (
//!     <base term>
//!     UNION [ALL | DISTINCT]
//!     <recursive term>
//! )
//! ```
//!
//! where the base term is an inline set of seed rows and the recursive term is
//! a single equi-join between the working table and an inline edge relation:
//!
//! ```sql
//! SELECT <projection>
//! FROM   <working> JOIN <edges>
//!        ON <working>.<parent_key> = <edges>.<from_column>
//! ```
//!
//! No arbitrary SQL parser is involved: plans arrive as typed structures
//! (deserialized at the API boundary and fully validated there).

use crate::batch::{check_value, Field, RecordBatch, Schema, Value};
use crate::error::{EngineError, Result};

/// `UNION` flavour.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum UnionOp {
    /// Bag semantics: duplicate rows survive every iteration.
    All,
    /// Set semantics: rows already accumulated never enter a working table
    /// again.
    Distinct,
}

impl UnionOp {
    /// Parse the JSON-facing keyword (case-insensitive).
    pub fn parse(s: &str) -> Option<Self> {
        match s.to_ascii_uppercase().as_str() {
            "ALL" => Some(UnionOp::All),
            "DISTINCT" | "" => Some(UnionOp::Distinct),
            _ => None,
        }
    }

    /// Keyword as used in SQL and JSON output.
    pub fn as_str(self) -> &'static str {
        match self {
            UnionOp::All => "ALL",
            UnionOp::Distinct => "DISTINCT",
        }
    }
}

/// Deterministic traversal strategy.
///
/// Both strategies emit the same multiset of rows; only their order and the
/// shape of the per-iteration trace differ.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TraversalOrder {
    /// Level-by-level fixpoint iteration (the classic semi-naïve loop).
    /// Children of one depth are fully expanded before any deeper row.
    BreadthFirst,
    /// Explicit LIFO stack: each row is expanded before its sibling.
    DepthFirst,
    /// Expand children in the order edges appear in the source relation.
    /// Still deterministic (the input order is part of the fixture).
    InputOrder,
}

impl TraversalOrder {
    /// Parse the JSON-facing keyword (case-insensitive); default BFS.
    pub fn parse(s: &str) -> Option<Self> {
        match s.to_ascii_lowercase().as_str() {
            "bfs" | "breadth_first" | "breadthfirst" => Some(TraversalOrder::BreadthFirst),
            "dfs" | "depth_first" | "depthfirst" => Some(TraversalOrder::DepthFirst),
            "input" | "input_order" | "inputorder" => Some(TraversalOrder::InputOrder),
            _ => None,
        }
    }

    /// Keyword used in JSON output.
    pub fn as_str(self) -> &'static str {
        match self {
            TraversalOrder::BreadthFirst => "bfs",
            TraversalOrder::DepthFirst => "dfs",
            TraversalOrder::InputOrder => "input",
        }
    }
}

/// One output-column binding inside the recursive `SELECT`.
#[derive(Clone, Debug, PartialEq)]
pub enum ProjectionExpr {
    /// Take the value of an edge-relation column by name.
    EdgeColumn(String),
    /// A typed constant.
    Literal(Value),
}

impl ProjectionExpr {
    /// Column name for trace/error messages.
    pub fn describe(&self) -> String {
        match self {
            ProjectionExpr::EdgeColumn(c) => format!("edge.{c}"),
            ProjectionExpr::Literal(v) => format!("literal({v})"),
        }
    }
}

/// The recursive term: working-table ⋈ edges, then a positional projection.
#[derive(Clone, Debug)]
pub struct RecursiveTerm {
    /// Inline edge relation.
    pub edges: RecordBatch,
    /// Column of the working CTE holding the parent join key.
    pub parent_key: String,
    /// Column of the edge relation holding the parent (source) key.
    pub edge_from: String,
    /// One binding per business column of the CTE, by position.
    pub projection: Vec<ProjectionExpr>,
}

/// Engine-managed path / cycle auxiliary columns.
///
/// These columns are **appended** to the business schema; the engine always
/// computes them itself. Cycle detection uses only [`PathSpec::key_columns`]
/// — never the whole row including the rendered path — so a repeating key is
/// caught even though the path string keeps growing.
#[derive(Clone, Debug)]
pub struct PathSpec {
    /// Declared identity columns whose repetition along one path marks a cycle.
    pub key_columns: Vec<String>,
    /// Name of the appended `utf8` column rendering the key path, e.g. `[1,2]`.
    pub path_column: String,
    /// Name of the appended `boolean` cycle-flag column.
    pub cycle_column: String,
}

/// A fully typed recursive-CTE plan (validation happens elsewhere).
#[derive(Clone, Debug)]
pub struct RecursivePlan {
    /// CTE name (used in logs and responses).
    pub name: String,
    /// Business-column schema (auxiliary path columns are not in here).
    pub schema: Schema,
    /// `UNION ALL` vs `UNION DISTINCT`.
    pub union_op: UnionOp,
    /// Traversal strategy.
    pub traversal: TraversalOrder,
    /// Base-term rows.
    pub seed: RecordBatch,
    /// Recursive-term operator.
    pub recursive: RecursiveTerm,
    /// Path/cycle configuration.
    pub path: PathSpec,
}

impl RecursivePlan {
    /// Index of the parent key column in the business schema.
    pub fn parent_key_index(&self) -> Result<usize> {
        self.schema.require_index(&self.recursive.parent_key)
    }

    /// Indices of the declared cycle key columns in the business schema.
    pub fn key_indices(&self) -> Result<Vec<usize>> {
        self.path
            .key_columns
            .iter()
            .map(|k| self.schema.require_index(k))
            .collect()
    }

    /// Resolve a projection expression to an edge column index / typed value.
    pub fn resolve_projection(&self) -> Result<Vec<ResolvedExpr>> {
        if self.recursive.projection.len() != self.schema.column_count() {
            return Err(EngineError::validation(format!(
                "recursive projection has {} expressions but CTE declares {} columns",
                self.recursive.projection.len(),
                self.schema.column_count()
            )));
        }
        self.recursive
            .projection
            .iter()
            .zip(self.schema.fields())
            .map(|(expr, field)| match expr {
                ProjectionExpr::EdgeColumn(col) => {
                    let idx = self.recursive.edges.schema().require_index(col.as_str())?;
                    let edge_field = &self.recursive.edges.schema().fields()[idx];
                    if edge_field.data_type != field.data_type {
                        return Err(EngineError::validation(format!(
                            "projection edge column '{}' ({}) cannot feed CTE column '{}' ({})",
                            col,
                            edge_field.data_type.as_str(),
                            field.name,
                            field.data_type.as_str()
                        )));
                    }
                    Ok(ResolvedExpr::EdgeColumn(idx))
                }
                ProjectionExpr::Literal(v) => {
                    check_value(v, field.data_type)?;
                    Ok(ResolvedExpr::Literal(v.clone()))
                }
            })
            .collect()
    }

    /// Output schema: business fields followed by path and cycle flag.
    pub fn output_schema(&self) -> Schema {
        let mut fields: Vec<Field> = self.schema.fields().to_vec();
        fields.push(Field::new(
            &self.path.path_column,
            crate::batch::DataType::Utf8,
        ));
        fields.push(Field::new(
            &self.path.cycle_column,
            crate::batch::DataType::Boolean,
        ));
        Schema::new(fields).expect("path/cycle name uniqueness validated beforehand")
    }
}

/// Projection expression with names resolved to indices.
#[derive(Clone, Debug)]
pub enum ResolvedExpr {
    /// Edge column index.
    EdgeColumn(usize),
    /// Typed constant.
    Literal(Value),
}
