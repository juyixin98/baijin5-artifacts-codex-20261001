//! Predicate evaluation over bitmap indexes.
//!
//! The executor binds an [`Expr`] to typed [`ColumnIndex`]es, evaluates each
//! node to a [`Tricolor`] over the index universe, then restricts the result
//! to the live row set at the requested version. A deleted row is in **none**
//! of TRUE/FALSE/UNKNOWN: the visible universe *is* the live bitmap, and the
//! post-restriction partition is verified against exactly that set.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};

use crate::error::{Result, TviError};
use crate::index::value_index::{ColumnIndex, Scalar};
use crate::index::{Tricolor, Version, VersionMap};
use crate::query::expr::Expr;

/// Monotonic run id, echoed in every trace line so logs from concurrent
/// requests can be attributed to a run.
static RUN_SEQ: AtomicU64 = AtomicU64::new(1);

/// Allocate the next run identity.
pub fn next_run_id() -> u64 {
    RUN_SEQ.fetch_add(1, Ordering::Relaxed)
}

/// Bound indexes for one evaluation.
pub struct Catalog<'a> {
    indexes: &'a HashMap<String, ColumnIndex>,
    versions: &'a VersionMap,
}

impl<'a> Catalog<'a> {
    /// Bind a catalog, enforcing the combined-index compatibility contract:
    /// every index must be built at the table's current generation over the
    /// same universe length.
    pub fn new(
        indexes: &'a HashMap<String, ColumnIndex>,
        versions: &'a VersionMap,
    ) -> Result<Self> {
        for (name, idx) in indexes {
            versions
                .ensure_compatible(idx.len(), idx.version())
                .map_err(|e| {
                    TviError::InvalidQuery(format!(
                        "index `{name}` is incompatible with the table universe: {e}"
                    ))
                })?;
        }
        Ok(Catalog { indexes, versions })
    }

    fn lookup(&self, column: &str) -> Result<&ColumnIndex> {
        self.indexes.get(column).ok_or_else(|| {
            TviError::UnknownColumn(format!(
                "{column} (known columns: {})",
                self.indexes.keys().cloned().collect::<Vec<_>>().join(", ")
            ))
        })
    }
}

/// Row counts and rows of a finished query.
#[derive(Debug, Clone)]
pub struct QueryOutcome {
    pub run_id: u64,
    pub as_of: Version,
    pub index_version: Version,
    pub universe_total: usize,
    pub live_total: usize,
    pub true_rows: Vec<usize>,
    pub false_rows: Vec<usize>,
    pub unknown_rows: Vec<usize>,
    pub deleted_rows: Vec<usize>,
    /// Classification restricted to live rows, length == live_total but keyed
    /// by logical row id via `live_order`.
    pub classifications: Vec<(usize, char)>,
}

impl QueryOutcome {
    /// WHERE-selected rows (TRUE only — SQL WHERE drops both FALSE and UNKNOWN).
    pub fn selected(&self) -> &[usize] {
        &self.true_rows
    }
}

/// Evaluate one expression tree.
pub fn evaluate(
    catalog: &Catalog<'_>,
    expr: &Expr,
    as_of: Option<Version>,
    run_id: u64,
) -> Result<QueryOutcome> {
    let as_of = as_of.unwrap_or_else(|| catalog.versions.head_version());
    tracing::info!(
        run_id,
        as_of,
        content_version = catalog.versions.index_version(),
        head_version = catalog.versions.head_version(),
        expr = %expr.describe(),
        "query start"
    );
    if as_of > catalog.versions.head_version() {
        return Err(TviError::InvalidQuery(format!(
            "as_of={as_of} is ahead of the table read head v{}",
            catalog.versions.head_version()
        )));
    }

    let raw = eval_node(catalog, expr, run_id, 0)?;
    tracing::debug!(
        run_id,
        t = %raw.t.to_bit_string(),
        f = %raw.f.to_bit_string(),
        u = %raw.u.to_bit_string(),
        "predicate evaluated over full index universe"
    );

    let live = catalog.versions.live_bitmap_at(as_of);
    tracing::info!(
        run_id,
        as_of,
        live = %live.to_bit_string(),
        live_count = live.count_ones(),
        "versioned live universe resolved"
    );

    let visible = raw.restrict_to_live(&live)?;
    tracing::info!(
        run_id,
        t = %visible.t.to_bit_string(),
        f = %visible.f.to_bit_string(),
        u = %visible.u.to_bit_string(),
        "3VL partition restricted to live rows"
    );

    let n = raw.len();
    let mut true_rows = Vec::new();
    let mut false_rows = Vec::new();
    let mut unknown_rows = Vec::new();
    let mut deleted_rows = Vec::new();
    let mut classifications = Vec::new();
    for row in 0..n {
        if !live.get(row) {
            deleted_rows.push(row);
        } else {
            let ch = if visible.t.get(row) {
                'T'
            } else if visible.f.get(row) {
                'F'
            } else {
                'U'
            };
            classifications.push((row, ch));
            match ch {
                'T' => true_rows.push(row),
                'F' => false_rows.push(row),
                _ => unknown_rows.push(row),
            }
        }
    }

    let outcome = QueryOutcome {
        run_id,
        as_of,
        index_version: catalog.versions.index_version(),
        universe_total: n,
        live_total: live.count_ones(),
        true_rows,
        false_rows,
        unknown_rows,
        deleted_rows,
        classifications,
    };
    tracing::info!(
        run_id,
        true_n = outcome.true_rows.len(),
        false_n = outcome.false_rows.len(),
        unknown_n = outcome.unknown_rows.len(),
        deleted_n = outcome.deleted_rows.len(),
        "query complete: every live row is in exactly one class"
    );
    Ok(outcome)
}

fn eval_node(catalog: &Catalog<'_>, expr: &Expr, run_id: u64, depth: usize) -> Result<Tricolor> {
    match expr {
        Expr::Cmp { column, op, value } => {
            let idx = catalog.lookup(column)?;
            let scalar = Scalar::coerce(idx.logical_type(), value).map_err(|e| match e {
                TviError::InvalidLiteral { value: v, .. } => TviError::InvalidLiteral {
                    column: column.clone(),
                    value: v,
                },
                TviError::TypeMismatch { expected, .. } => TviError::TypeMismatch {
                    column: column.clone(),
                    expected,
                    found: json_kind(value),
                },
                other => other,
            })?;
            let tri = idx.evaluate(*op, &scalar)?;
            tracing::debug!(
                run_id,
                depth,
                node = %expr.describe(),
                t = %tri.t.to_bit_string(),
                f = %tri.f.to_bit_string(),
                u = %tri.u.to_bit_string(),
                "leaf comparison evaluated"
            );
            Ok(tri)
        }
        Expr::IsNull { column, negate } => {
            let idx = catalog.lookup(column)?;
            let tri = idx.evaluate_is_null(*negate);
            tracing::debug!(
                run_id,
                depth,
                node = %expr.describe(),
                t = %tri.t.to_bit_string(),
                f = %tri.f.to_bit_string(),
                u = %tri.u.to_bit_string(),
                "leaf null-test evaluated"
            );
            Ok(tri)
        }
        Expr::Not(inner) => {
            let x = eval_node(catalog, inner, run_id, depth + 1)?;
            let r = !x;
            tracing::debug!(
                run_id,
                depth,
                node = %expr.describe(),
                step = "NOT swaps TRUE/FALSE, UNKNOWN is a fixed point (no machine-word NOT)",
                t = %r.t.to_bit_string(),
                f = %r.f.to_bit_string(),
                u = %r.u.to_bit_string(),
                "not evaluated"
            );
            Ok(r)
        }
        Expr::And(args) => combine(catalog, args, run_id, depth, expr, true),
        Expr::Or(args) => combine(catalog, args, run_id, depth, expr, false),
    }
}

fn combine(
    catalog: &Catalog<'_>,
    args: &[Expr],
    run_id: u64,
    depth: usize,
    expr: &Expr,
    is_and: bool,
) -> Result<Tricolor> {
    let label = if is_and { "AND" } else { "OR" };
    let mut acc = eval_node(catalog, &args[0], run_id, depth + 1)?;
    for (i, arg) in args.iter().enumerate().skip(1) {
        let rhs = eval_node(catalog, arg, run_id, depth + 1)?;
        acc = if is_and {
            acc.and(&rhs)?
        } else {
            acc.or(&rhs)?
        };
        tracing::debug!(
            run_id,
            depth,
            node = %expr.describe(),
            step = i + 1,
            gate = if is_and {
                "FALSE dominates; UNKNOWN beats TRUE"
            } else {
                "TRUE dominates; UNKNOWN beats FALSE"
            },
            t = %acc.t.to_bit_string(),
            f = %acc.f.to_bit_string(),
            u = %acc.u.to_bit_string(),
            "{label} step {i} merged"
        );
    }
    Ok(acc)
}

fn json_kind(v: &serde_json::Value) -> &'static str {
    match v {
        serde_json::Value::Null => "null",
        serde_json::Value::Bool(_) => "boolean",
        serde_json::Value::Number(_) => "number",
        serde_json::Value::String(_) => "string",
        serde_json::Value::Array(_) => "array",
        serde_json::Value::Object(_) => "object",
    }
}
