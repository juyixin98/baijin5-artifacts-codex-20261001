//! Query operators: two genuinely independent execution strategies.
//!
//! * [`decorrelated`] — the compiled plan. The inner relation is scanned once
//!   and grouped by the correlation key (`HashMap`); the outer is scanned once
//!   and probes the groups. No subquery re-execution per outer row.
//! * [`row_by_row`] — the reference interpreter. For every outer row it
//!   re-evaluates the correlation by a nested loop over the inner relation,
//!   exactly as the textual SQL (`dependent join` + unnest) would execute.
//!
//! The two strategies share only [`eval_group`] — the semantics of turning one
//! matched group into a scalar. *How a group is found* is independently
//! implemented: hash probe vs. pairwise nested loop. The API and the test
//! suite compare both results and both failure categories.

use std::collections::HashMap;

use crate::batch::{sql_key_equal, Column, LogicalType, RecordBatch, ScalarValue};
use crate::catalog::Catalog;
use crate::error::{EngineError, EngineResult};
use crate::plan::{AggKind, QueryPlan, SubqueryKind};

/// Resolved relations and indices for one execution.
pub struct PreparedQuery {
    outer: RecordBatch,
    projected: RecordBatch,
    inner: RecordBatch,
    outer_key_indices: Vec<usize>,
    inner_key_indices: Vec<usize>,
    kind: SubqueryKind,
    output_column: String,
    output_type: LogicalType,
    out_columns: Vec<(String, LogicalType)>,
}

pub fn prepare(plan: &QueryPlan, catalog: &Catalog) -> EngineResult<PreparedQuery> {
    let outer_rel = catalog.get(&plan.outer_relation)?;
    let inner_rel = catalog.get(&plan.subquery.inner_relation)?;

    let outer = (*outer_rel.batch).clone();
    let inner = (*inner_rel.batch).clone();

    // Outer projection indices (empty select was expanded to all columns).
    let project_idx: Vec<usize> = plan
        .outer_select
        .iter()
        .map(|n| outer.column_index(n))
        .collect::<EngineResult<Vec<_>>>()?;
    let projected = outer.project(&project_idx)?;

    let outer_key_indices = plan
        .subquery
        .correlation
        .iter()
        .map(|c| outer.column_index(&c.outer_column))
        .collect::<EngineResult<Vec<_>>>()?;
    let inner_key_indices = plan
        .subquery
        .correlation
        .iter()
        .map(|c| inner.column_index(&c.inner_column))
        .collect::<EngineResult<Vec<_>>>()?;

    // Determine output type.
    let inner_value_type = match &plan.subquery.kind {
        SubqueryKind::Scalar { value_column } => inner.column(value_column)?.logical_type(),
        SubqueryKind::ScalarAggregate { agg, value_column } => match agg {
            AggKind::Count => LogicalType::Integer,
            AggKind::Sum => inner.column(value_column.as_ref().unwrap())?.logical_type(),
        },
        SubqueryKind::Exists | SubqueryKind::NotExists => LogicalType::Boolean,
    };
    let output_type = plan.subquery.kind.output_type(inner_value_type);

    let mut out_columns: Vec<(String, LogicalType)> = project_idx
        .iter()
        .map(|&i| {
            let col = &outer.columns()[i];
            (col.name().to_string(), col.logical_type())
        })
        .collect();
    out_columns.push((plan.subquery.output_column.clone(), output_type));

    Ok(PreparedQuery {
        outer,
        projected,
        inner,
        outer_key_indices,
        inner_key_indices,
        kind: plan.subquery.kind.clone(),
        output_column: plan.subquery.output_column.clone(),
        output_type,
        out_columns,
    })
}

impl PreparedQuery {
    pub fn outer_rows(&self) -> usize {
        self.outer.row_count()
    }
}

fn finish(prep: &PreparedQuery, appended: Vec<ScalarValue>) -> EngineResult<RecordBatch> {
    let col = Column::from_scalars(&prep.output_column, prep.output_type, appended)?;
    let batch = prep.projected.with_column(col)?;
    debug_assert_eq!(batch.row_count(), prep.outer.row_count());
    Ok(batch)
}

// ---------------------------------------------------------------------------
// Executor 1: decorrelated group-probe (inner grouped once, outer probes once)
// ---------------------------------------------------------------------------

pub fn decorrelated(prep: &PreparedQuery) -> EngineResult<RecordBatch> {
    // 1. Group the inner relation by its correlation key, in a single scan.
    //    Keys containing NULL are still grouped (correct physical grouping),
    //    but SQL equality below means an outer NULL key will never probe them.
    let mut groups: HashMap<Vec<ScalarValue>, Vec<usize>> =
        HashMap::with_capacity(prep.inner.row_count());
    for row in 0..prep.inner.row_count() {
        let key = prep.inner.row_key(row, &prep.inner_key_indices);
        groups.entry(key).or_default().push(row);
    }

    // 2. Probe once per outer row (outer row multiplicity is untouched).
    let mut appended = Vec::with_capacity(prep.outer.row_count());
    for row in 0..prep.outer.row_count() {
        let outer_key = prep.outer.row_key(row, &prep.outer_key_indices);
        let matched: &[usize] = if outer_key.iter().any(|v| v == &ScalarValue::Null) {
            // SQL: NULL = x is UNKNOWN for every conjunct -> no match.
            &[]
        } else {
            groups.get(&outer_key).map(|v| v.as_slice()).unwrap_or(&[])
        };
        appended.push(eval_group(&prep.kind, &prep.inner, matched, row)?);
    }

    finish(prep, appended)
}

// ---------------------------------------------------------------------------
// Executor 2: independent nested-loop, per-outer-row interpreter
// ---------------------------------------------------------------------------

pub fn row_by_row(prep: &PreparedQuery) -> EngineResult<RecordBatch> {
    let mut appended = Vec::with_capacity(prep.outer.row_count());
    for outer_row in 0..prep.outer.row_count() {
        // Re-evaluate the correlation against the full inner relation.
        let mut matched = Vec::new();
        for inner_row in 0..prep.inner.row_count() {
            let mut all_eq = true;
            for (&ici, &oci) in prep
                .inner_key_indices
                .iter()
                .zip(prep.outer_key_indices.iter())
            {
                let iv = prep.inner.columns()[ici].value(inner_row);
                let ov = prep.outer.columns()[oci].value(outer_row);
                if !sql_key_equal(&iv, &ov) {
                    all_eq = false;
                    break;
                }
            }
            if all_eq {
                matched.push(inner_row);
            }
        }
        appended.push(eval_group(&prep.kind, &prep.inner, &matched, outer_row)?);
    }
    finish(prep, appended)
}

// ---------------------------------------------------------------------------
// Shared group semantics (the only code common to both executors)
// ---------------------------------------------------------------------------

fn eval_group(
    kind: &SubqueryKind,
    inner: &RecordBatch,
    matched: &[usize],
    outer_row: usize,
) -> EngineResult<ScalarValue> {
    match kind {
        SubqueryKind::Exists => Ok(ScalarValue::Bool(!matched.is_empty())),
        SubqueryKind::NotExists => Ok(ScalarValue::Bool(matched.is_empty())),
        SubqueryKind::Scalar { value_column } => match matched.len() {
            0 => Ok(ScalarValue::Null),
            1 => Ok(inner.column(value_column)?.value(matched[0])),
            n => Err(EngineError::ScalarCardinality { outer_row, rows: n }),
        },
        SubqueryKind::ScalarAggregate { agg, value_column } => match agg {
            // COUNT(*): cardinality; empty group is 0, never NULL.
            AggKind::Count => Ok(ScalarValue::Int(matched.len() as i64)),
            // SUM: NULL inputs ignored; empty (or all-NULL) group is NULL.
            AggKind::Sum => {
                let col = inner.column(value_column.as_ref().unwrap())?;
                let mut acc: Option<i64> = None;
                for &r in matched {
                    if let ScalarValue::Int(v) = col.value(r) {
                        acc = Some(match acc {
                            None => v,
                            Some(a) => a.checked_add(v).ok_or_else(|| {
                                EngineError::execution(format!(
                                    "SUM overflow at outer row {outer_row}"
                                ))
                            })?,
                        });
                    }
                }
                Ok(match acc {
                    Some(v) => ScalarValue::Int(v),
                    None => ScalarValue::Null,
                })
            }
        },
    }
}

/// Output column names/types for response rendering.
pub fn output_schema(prep: &PreparedQuery) -> &[(String, LogicalType)] {
    &prep.out_columns
}
