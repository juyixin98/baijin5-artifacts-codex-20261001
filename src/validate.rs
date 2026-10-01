//! Execution-front validation: the *only* place request shapes and parameters
//! are checked. Everything downstream consumes a [`QueryPlan`] whose invariants
//! hold by construction.
//!
//! Quantile bounds are rejected here, before any allocation or state creation.

use crate::error::{ErrorKind, PctlError, Result};
use crate::spec::{
    BoundOperator, LogicalType, OperatorSpec, PercentileMethod, QueryPlan, QueryRequest, SortOrder,
};

/// Types that each operator accepts as its measure column.
fn accepted_types(op: &OperatorSpec) -> &'static [LogicalType] {
    match op {
        OperatorSpec::Percentile { method, .. } => match method {
            PercentileMethod::Continuous => &[LogicalType::I64, LogicalType::F64],
            PercentileMethod::Discrete => &[LogicalType::I64, LogicalType::F64, LogicalType::Utf8],
        },
        OperatorSpec::Mode { .. } => &[LogicalType::I64, LogicalType::F64, LogicalType::Utf8],
        OperatorSpec::StringAgg { .. } => &[LogicalType::Utf8],
    }
}

pub fn validate_request(req: &QueryRequest) -> Result<QueryPlan> {
    // ---- columns ----------------------------------------------------------------
    if req.columns.is_empty() {
        return Err(PctlError::validation(
            "empty_columns",
            "request must contain at least one column",
        ));
    }
    for (i, c) in req.columns.iter().enumerate() {
        if c.name.is_empty() {
            return Err(PctlError::validation(
                "empty_column_name",
                "column names must be non-empty",
            )
            .at(format!("columns[{i}].name")));
        }
        for (j, other) in req.columns.iter().enumerate() {
            if i != j && c.name == other.name {
                return Err(PctlError::validation(
                    "duplicate_column_name",
                    format!("column name {:?} appears more than once", c.name),
                )
                .at(format!("columns[{i}].name")));
            }
        }
    }

    let find_column = |name: &str| -> Result<usize> {
        req.columns
            .iter()
            .position(|c| c.name == name)
            .ok_or_else(|| {
                PctlError::validation(
                    "unknown_column",
                    format!("column {name:?} is not declared in `columns`"),
                )
            })
    };

    // ---- group by ---------------------------------------------------------------
    let group_column_index = find_column(&req.group_by).map_err(|e| e.at("group_by"))?;
    let group_type = req.columns[group_column_index].data_type;
    if !matches!(group_type, LogicalType::I64 | LogicalType::Utf8) {
        return Err(PctlError::validation(
            "unsupported_group_type",
            format!(
                "GROUP BY only supports i64 and utf8, got {}",
                group_type.as_str()
            ),
        )
        .at("group_by"));
    }

    // ---- operators --------------------------------------------------------------
    if req.operators.is_empty() {
        return Err(PctlError::validation(
            "empty_operators",
            "request must contain at least one operator",
        ));
    }
    let mut bound = Vec::with_capacity(req.operators.len());
    for (i, op) in req.operators.iter().enumerate() {
        let (column, extra) = match op {
            OperatorSpec::Percentile { column, p, method } => {
                // Quantile bounds rejected BEFORE execution, not mid-query.
                if !p.is_finite() {
                    return Err(PctlError::validation(
                        "quantile_not_finite",
                        format!("percentile p must be a finite number in [0, 1], got {p}"),
                    )
                    .at(format!("operators[{i}].p")));
                }
                if !(0.0..=1.0).contains(p) {
                    return Err(PctlError::validation(
                        "quantile_out_of_range",
                        format!("percentile p must lie in [0, 1], got {p}"),
                    )
                    .at(format!("operators[{i}].p")));
                }
                (column, format!("percentile_{:?}", method).to_lowercase())
            }
            OperatorSpec::Mode { column } => (column, "mode".into()),
            OperatorSpec::StringAgg {
                column,
                delimiter,
                order,
            } => {
                if delimiter.is_empty() {
                    return Err(PctlError::validation(
                        "empty_delimiter",
                        "string_agg delimiter must contain at least one byte",
                    )
                    .at(format!("operators[{i}].delimiter")));
                }
                if !matches!(order, SortOrder::Asc | SortOrder::Desc) {
                    return Err(PctlError::validation(
                        "bad_order",
                        "string_agg order must be `asc` or `desc`",
                    )
                    .at(format!("operators[{i}].order")));
                }
                (column, "string_agg".into())
            }
        };
        let column_index =
            find_column(column).map_err(|e| e.at(format!("operators[{i}].column")))?;
        let column_type = req.columns[column_index].data_type;
        let accepted = accepted_types(op);
        if !accepted.contains(&column_type) {
            return Err(PctlError::validation(
                "operator_type_mismatch",
                format!(
                    "{extra} accepts [{}], column {column:?} is {}",
                    accepted
                        .iter()
                        .map(|t| t.as_str())
                        .collect::<Vec<_>>()
                        .join(", "),
                    column_type.as_str()
                ),
            )
            .at(format!("operators[{i}].column")));
        }
        bound.push(BoundOperator {
            index: i,
            spec: op.clone(),
            column_index,
            column_type,
        });
    }

    // ---- hints ------------------------------------------------------------------
    if let Some(budget) = req.hints.memory_budget_bytes {
        if budget < 256 {
            return Err(PctlError::new(
                ErrorKind::Validation,
                "hint_budget_too_small",
                "hints.memory_budget_bytes must be >= 256",
            )
            .at("hints.memory_budget_bytes"));
        }
    }
    if let Some(token) = &req.hints.resume {
        if token.ordinal_cursor == 0 && token.spill_dir.is_empty() {
            return Err(PctlError::validation(
                "bad_resume_token",
                "resume.spill_dir must be non-empty",
            )
            .at("hints.resume.spill_dir"));
        }
    }

    Ok(QueryPlan {
        group_column_index,
        group_type,
        operators: bound,
    })
}
