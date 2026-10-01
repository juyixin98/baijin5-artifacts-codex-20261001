//! Naive, row-at-a-time nested-loop interpreter.
//!
//! This is the **independent reference implementation** of the semantics.
//! It contains no join, grouping or decorrelation: for every outer row it
//! re-scans the inner relation from scratch and evaluates correlation
//! predicates directly with SQL three-valued logic.
//!
//! The decorrelated executor in [`crate::rewrite`] is checked against this
//! module on many fixtures. The two are deliberately written against only the
//! shared low-level primitives ([`crate::operators`]) so that agreement is
//! evidence for the rewrite rather than shared code pretending to be two
//! implementations.

use std::sync::Arc;

use crate::batch::{Batch, Column, Field, Scalar, Schema};
use crate::error::{ErrorKind, QError, QResult};
use crate::operators::agg::AggAcc;
use crate::operators::{eval_cmp_values, eval_comparison, eval_expr, is_true};
use crate::query::SubForm;
use crate::validator::{RInnerTerm, ROuterTerm, RSubquery, ResolvedQuery};

/// Execute the query by nested-loop interpretation and return projected rows.
pub fn execute(q: &ResolvedQuery) -> QResult<Batch> {
    let outer = q.outer_batch.as_ref();
    let term = &q.terms[q.subquery_at];

    // Output rows are outer row indices that survive, preserving order and
    // multiplicity.
    let mut kept: Vec<usize> = Vec::new();

    for orow in 0..outer.row_count() {
        // Outer-local predicates (evaluated in the order given).
        let mut locals_ok = true;
        for t in &q.terms {
            if let ROuterTerm::Local(cmp) = t {
                if !is_true(eval_comparison(cmp, outer, orow)?) {
                    locals_ok = false;
                    break;
                }
            }
        }
        if !locals_ok {
            continue;
        }

        let predicate = match term {
            ROuterTerm::Exists { sub, negated } => {
                let found = scan_matches(sub, outer, orow, |_| Ok(()))?;
                let v = found > 0;
                if *negated {
                    !v
                } else {
                    v
                }
            }
            ROuterTerm::ScalarSub { outer: oe, op, sub } => {
                let scalar = eval_scalar(sub, outer, orow)?;
                let ov = eval_expr(oe, outer, orow);
                // Three-valued comparison; only TRUE keeps the row.
                matches!(eval_cmp_values(*op, &ov, &scalar)?, Some(true))
            }
            ROuterTerm::InSub { outer: oe, sub, .. } => {
                let ov = eval_expr(oe, outer, orow);
                eval_in(sub, outer, orow, &ov)? == Some(true)
            }
            ROuterTerm::Local(_) => {
                return Err(QError::internal(
                    "subquery term index points at a local predicate",
                ));
            }
        };

        if predicate {
            kept.push(orow);
        }
    }

    project(q, outer, &kept)
}

/// Scan the inner relation for rows matching outer row `orow` on local
/// predicates and correlation equalities. `each` is invoked (in inner row
/// order) for every matching row. Returns the match count.
fn scan_matches(
    sub: &RSubquery,
    outer: &Batch,
    orow: usize,
    mut each: impl FnMut(usize) -> QResult<()>,
) -> QResult<usize> {
    let inner = sub.inner_batch.as_ref();
    let mut count = 0usize;
    'inner: for irow in 0..inner.row_count() {
        for t in &sub.terms {
            match t {
                RInnerTerm::Local(cmp) => {
                    if !is_true(eval_comparison(cmp, inner, irow)?) {
                        continue 'inner;
                    }
                }
                RInnerTerm::Corr {
                    outer: oc,
                    inner: ic,
                } => {
                    let ov = outer.get(oc.index, orow).clone();
                    let iv = inner.get(ic.index, irow).clone();
                    // Correlation equality must be TRUE, not FALSE/UNKNOWN.
                    if ov.sql_eq(&iv)? != Some(true) {
                        continue 'inner;
                    }
                }
            }
        }
        count += 1;
        each(irow)?;
    }
    Ok(count)
}

/// Scalar subquery value for one outer row.
///
/// * aggregate form: one value even when zero rows match
///   (`COUNT -> 0`, `SUM -> NULL`);
/// * bare projection form: zero rows -> NULL, one row -> its value,
///   more than one row -> [`ErrorKind::ScalarMultipleRows`].
fn eval_scalar(sub: &RSubquery, outer: &Batch, orow: usize) -> QResult<Scalar> {
    if let Some((func, _arg)) = &sub.aggregate {
        let mut acc = AggAcc::new(*func);
        scan_matches(sub, outer, orow, |irow| {
            // Inner rows are scanned in ascending order in both engines, so
            // overflow timing is identical.
            let v = sub
                .aggregate
                .as_ref()
                .map(|(_, c)| sub.inner_batch.get(c.index, irow).clone())
                .unwrap_or(Scalar::Null);
            acc.update(&v)
        })?;
        return Ok(acc.finish());
    }

    // Bare scalar subquery: enforce single-row cardinality.
    let proj = sub
        .project
        .as_ref()
        .ok_or_else(|| QError::internal("scalar subquery has neither aggregate nor projection"))?;
    let mut found: Option<Scalar> = None;
    scan_matches(sub, outer, orow, |irow| {
        let v = sub.inner_batch.get(proj.index, irow).clone();
        match &found {
            None => {
                found = Some(v);
                Ok(())
            }
            Some(_) => Err(QError::new(
                ErrorKind::ScalarMultipleRows,
                "scalar subquery returned more than one row for an outer row",
            )),
        }
    })?;
    Ok(found.unwrap_or(Scalar::Null))
}

/// NULL-aware `outer IN (SELECT p FROM inner ...)` for one outer row.
///
/// Returns `Some(true)` (found an equal), `Some(false)` (definitely absent),
/// or `None` (UNKNOWN: no equal but a NULL present).
fn eval_in(sub: &RSubquery, outer: &Batch, orow: usize, ov: &Scalar) -> QResult<Option<bool>> {
    if ov.is_null() {
        return Ok(None);
    }
    let proj = sub
        .project
        .as_ref()
        .ok_or_else(|| QError::internal("IN subquery has no resolved projection"))?;
    let mut saw_null = false;
    let mut found = false;
    scan_matches(sub, outer, orow, |irow| {
        if found {
            return Ok(());
        }
        let iv = sub.inner_batch.get(proj.index, irow).clone();
        match ov.sql_eq(&iv)? {
            Some(true) => found = true,
            None => saw_null = true,
            Some(false) => {}
        }
        Ok(())
    })?;
    if found {
        Ok(Some(true))
    } else if saw_null {
        Ok(None)
    } else {
        Ok(Some(false))
    }
}

fn project(q: &ResolvedQuery, outer: &Batch, kept: &[usize]) -> QResult<Batch> {
    let fields: Vec<Field> = q
        .select
        .iter()
        .map(|s| outer.schema().fields[s.index].clone())
        .collect();
    let mut columns: Vec<Column> = q
        .select
        .iter()
        .map(|_| Vec::with_capacity(kept.len()))
        .collect();
    for &orow in kept {
        for (ci, s) in q.select.iter().enumerate() {
            columns[ci].push(outer.get(s.index, orow).clone());
        }
    }
    Batch::try_new(Arc::new(Schema::new(fields)), columns)
}

/// The form this query executes (for traces and tests).
pub fn form_of(q: &ResolvedQuery) -> SubForm {
    q.form
}
