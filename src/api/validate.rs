//! Validation entry point: request JSON types -> typed, checked plan.
//!
//! Everything an external caller can influence is checked here: names, types,
//! arities, key references, join columns, projection bindings and limits.
//! Execution receives only structures that already satisfy its invariants.

use crate::batch::{check_value, DataType, Field, RecordBatch, Schema, Value};
use crate::config::Config;
use crate::error::{EngineError, Result};
use crate::plan::{
    PathSpec, ProjectionExpr, RecursivePlan, RecursiveTerm, TraversalOrder, UnionOp,
};
use crate::state::Limits;

use super::types::{ColumnDef, ExecuteRequest, LimitsDef, ProjectionDef, RecursiveTermDef};

/// Validated run inputs.
pub struct ValidatedRun {
    /// Typed plan.
    pub plan: RecursivePlan,
    /// Effective limits.
    pub limits: Limits,
}

/// Validate an [`ExecuteRequest`] against process configuration.
pub fn validate_request(req: ExecuteRequest, config: &Config) -> Result<ValidatedRun> {
    if req.cte_name.trim().is_empty() {
        return Err(EngineError::validation("cte_name must be non-empty"));
    }
    let union_op = parse_union(&req.union_op)?;
    let traversal = if req.order.trim().is_empty() {
        config.default_traversal
    } else {
        TraversalOrder::parse(&req.order).ok_or_else(|| {
            EngineError::validation(format!("unknown traversal order '{}'", req.order))
        })?
    };

    let schema = build_schema(&req.schema, "CTE schema")?;
    if req.path_column == req.cycle_column {
        return Err(EngineError::validation(
            "path_column and cycle_column must have distinct names",
        ));
    }
    if schema.index_of(&req.path_column).is_some() {
        return Err(EngineError::validation(format!(
            "path_column '{}' collides with a business column",
            req.path_column
        )));
    }
    if schema.index_of(&req.cycle_column).is_some() {
        return Err(EngineError::validation(format!(
            "cycle_column '{}' collides with a business column",
            req.cycle_column
        )));
    }
    if req.key_columns.is_empty() {
        return Err(EngineError::validation(
            "key_columns must name at least one business column for cycle marking",
        ));
    }
    let mut seen_keys = std::collections::HashSet::new();
    for key in &req.key_columns {
        schema.require_index(key)?;
        if !seen_keys.insert(key.as_str()) {
            return Err(EngineError::validation(format!(
                "key_columns lists '{key}' more than once"
            )));
        }
    }

    let seed = build_batch(&schema, req.seed, "seed relation")?;
    let edge_schema = build_schema(&req.edges.schema, "edge schema")?;
    let edges = build_batch(&edge_schema, req.edges.rows, "edge relation")?;

    let recursive = validate_recursive(req.recursive, &schema, &edges)?;

    let limits = validate_limits(req.limits, config)?;

    let plan = RecursivePlan {
        name: req.cte_name,
        schema,
        union_op,
        traversal,
        seed,
        recursive,
        path: PathSpec {
            key_columns: req.key_columns,
            path_column: req.path_column,
            cycle_column: req.cycle_column,
        },
    };
    Ok(ValidatedRun { plan, limits })
}

fn parse_union(raw: &str) -> Result<UnionOp> {
    if raw.trim().is_empty() {
        return Ok(UnionOp::Distinct);
    }
    UnionOp::parse(raw).ok_or_else(|| {
        EngineError::validation(format!("union must be 'ALL' or 'DISTINCT', got '{raw}'"))
    })
}

fn validate_limits(overrides: Option<LimitsDef>, config: &Config) -> Result<Limits> {
    let d = overrides.unwrap_or(LimitsDef {
        max_depth: None,
        max_rows: None,
    });
    let max_depth = d.max_depth.unwrap_or(config.max_depth);
    let max_rows = d.max_rows.unwrap_or(config.max_rows);
    if max_depth > config.max_depth {
        return Err(EngineError::validation(format!(
            "max_depth={max_depth} exceeds process ceiling {}",
            config.max_depth
        )));
    }
    if max_rows > config.max_rows {
        return Err(EngineError::validation(format!(
            "max_rows={max_rows} exceeds process ceiling {}",
            config.max_rows
        )));
    }
    Limits::new(max_depth, max_rows)
}

fn validate_recursive(
    def: RecursiveTermDef,
    cte_schema: &Schema,
    edges: &RecordBatch,
) -> Result<RecursiveTerm> {
    let parent_col_idx = cte_schema.require_index(&def.parent_key)?;
    let edge_from_idx = edges.schema().require_index(&def.edge_from)?;
    if cte_schema.fields()[parent_col_idx].data_type
        != edges.schema().fields()[edge_from_idx].data_type
    {
        return Err(EngineError::validation(format!(
            "join key type mismatch: working.{} ({}) vs edge.{} ({})",
            def.parent_key,
            cte_schema.fields()[parent_col_idx].data_type.as_str(),
            def.edge_from,
            edges.schema().fields()[edge_from_idx].data_type.as_str()
        )));
    }
    let edge_schema = edges.schema();

    if def.projection.len() != cte_schema.column_count() {
        return Err(EngineError::validation(format!(
            "recursive projection has {} bindings but CTE declares {} columns",
            def.projection.len(),
            cte_schema.column_count()
        )));
    }
    if def.projection.is_empty() {
        return Err(EngineError::validation(
            "recursive projection must contain at least one binding",
        ));
    }
    let projection = def
        .projection
        .into_iter()
        .enumerate()
        .map(|(i, p)| resolve_binding(p, edge_schema, cte_schema, i))
        .collect::<Result<Vec<_>>>()?;

    // The parent key column must itself be fed from an edge column — otherwise
    // the join key could never propagate and traversal would silently stop.
    match &projection[parent_col_idx] {
        ProjectionExpr::EdgeColumn(_) => {}
        ProjectionExpr::Literal(_) => {
            return Err(EngineError::validation(format!(
                "recursive projection for parent key column '{}' must reference an edge column, \
                 not a literal (otherwise traversal cannot propagate keys)",
                def.parent_key
            )));
        }
    }

    Ok(RecursiveTerm {
        edges: edges.clone(),
        parent_key: def.parent_key,
        edge_from: def.edge_from,
        projection,
    })
}

fn resolve_binding(
    def: ProjectionDef,
    edge_schema: &Schema,
    cte_schema: &Schema,
    position: usize,
) -> Result<ProjectionExpr> {
    let target = &cte_schema.fields()[position];
    match (def.edge_column, def.literal) {
        (Some(col), None) => {
            let idx = edge_schema.require_index(&col)?;
            let src = &edge_schema.fields()[idx];
            if src.data_type != target.data_type {
                return Err(EngineError::validation(format!(
                    "projection position {} takes edge column '{}' ({}) but CTE column '{}' is {}",
                    position,
                    col,
                    src.data_type.as_str(),
                    target.name,
                    target.data_type.as_str()
                )));
            }
            Ok(ProjectionExpr::EdgeColumn(col))
        }
        (None, Some(json)) => {
            let value = coerce_scalar(&json, target.data_type)?;
            check_value(&value, target.data_type)?;
            Ok(ProjectionExpr::Literal(value))
        }
        (None, None) => Err(EngineError::validation(format!(
            "projection position {} for '{}' needs exactly one of edge_column/literal",
            position, target.name
        ))),
        (Some(_), Some(_)) => Err(EngineError::validation(format!(
            "projection position {} for '{}' sets both edge_column and literal",
            position, target.name
        ))),
    }
}

fn build_schema(defs: &[ColumnDef], context: &str) -> Result<Schema> {
    if defs.is_empty() {
        return Err(EngineError::validation(format!(
            "{context} must declare columns"
        )));
    }
    let fields = defs
        .iter()
        .map(|c| {
            let ty = DataType::parse(&c.data_type).ok_or_else(|| {
                EngineError::validation(format!(
                    "{context}: column '{}' has unknown type '{}' (want int64|utf8|boolean)",
                    c.name, c.data_type
                ))
            })?;
            Ok(Field::new(&c.name, ty))
        })
        .collect::<Result<Vec<_>>>()?;
    Schema::new(fields)
}

fn build_batch(
    schema: &Schema,
    rows: Vec<Vec<serde_json::Value>>,
    context: &str,
) -> Result<RecordBatch> {
    let typed = rows
        .into_iter()
        .enumerate()
        .map(|(row_idx, raw_row)| {
            if raw_row.len() != schema.column_count() {
                return Err(EngineError::validation(format!(
                    "{context}: row {row_idx} has {} values, schema expects {}",
                    raw_row.len(),
                    schema.column_count()
                )));
            }
            raw_row
                .into_iter()
                .zip(schema.fields())
                .map(|(json, field)| {
                    let value = coerce_scalar(&json, field.data_type).map_err(|e| {
                        EngineError::validation(format!(
                            "{context} row {row_idx} column '{}': {e}",
                            field.name
                        ))
                    })?;
                    check_value(&value, field.data_type).map_err(|e| {
                        EngineError::validation(format!(
                            "{context} row {row_idx} column '{}': {e}",
                            field.name
                        ))
                    })?;
                    Ok(value)
                })
                .collect::<Result<Vec<_>>>()
        })
        .collect::<Result<Vec<_>>>()?;
    RecordBatch::from_rows(schema.clone(), typed)
}

/// Coerce one JSON scalar to a [`Value`] of the declared type.
///
/// JSON `null` becomes SQL `NULL` for any column. Numbers must be exact
/// integers within `i64`; strings never auto-coerce and vice versa.
pub fn coerce_scalar(json: &serde_json::Value, ty: DataType) -> Result<Value> {
    match (json, ty) {
        (serde_json::Value::Null, _) => Ok(Value::Null),
        (serde_json::Value::Bool(b), DataType::Boolean) => Ok(Value::Boolean(*b)),
        (serde_json::Value::Number(n), DataType::Int64) => n
            .as_i64()
            .map(Value::Int64)
            .ok_or_else(|| EngineError::validation(format!("number {n} is not an int64"))),
        (serde_json::Value::String(s), DataType::Utf8) => Ok(Value::Utf8(s.clone())),
        (json, ty) => Err(EngineError::validation(format!(
            "cannot coerce JSON {} to declared type {}",
            json_type_name(json),
            ty.as_str()
        ))),
    }
}

fn json_type_name(v: &serde_json::Value) -> &'static str {
    match v {
        serde_json::Value::Null => "null",
        serde_json::Value::Bool(_) => "boolean",
        serde_json::Value::Number(_) => "number",
        serde_json::Value::String(_) => "string",
        serde_json::Value::Array(_) => "array",
        serde_json::Value::Object(_) => "object",
    }
}
