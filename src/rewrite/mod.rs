//! Decorrelation by unnesting.
//!
//! Given a validated query with one correlated subquery predicate, this
//! module compiles the correlated nested evaluation into group/join-style
//! bulk evaluation:
//!
//! 1. Build a hash group table of the inner relation on the inner-side
//!    correlation columns ([`GroupTable`]), applying inner-local predicates
//!    and dropping inner rows whose correlation key is NULL (a `=` correlation
//!    can never be TRUE for them).
//! 2. Scan each outer row once. Apply outer-local predicates. Look its
//!    correlation key up in the group table — a NULL outer key finds no group,
//!    exactly as the nested loop would.
//! 3. Evaluate the subquery predicate from the single matched group:
//!      - EXISTS / NOT EXISTS from group presence;
//!      - aggregate scalar from the group aggregate;
//!      - bare scalar from the group's single projected value, erroring on
//!        cardinality > 1;
//!      - IN with NULL-aware matching inside the group.
//!
//! This is the classical magic-set / group-by de-correlation: the correlated
//! subquery becomes `Outer [LeftSemi/Anti/Left] GroupedInner ON corr_key`,
//! with outer rows emitted in original order and multiplicity so duplicates
//! survive.
//!
//! The proofs of why each emitted rule is equivalent to nested-loop
//! evaluation are collected in [`proofs`].

use std::sync::Arc;

use crate::batch::{Batch, Column, Field, Scalar, Schema};
use crate::error::{ErrorKind, QError, QResult};
use crate::operators::agg::AggAcc;
use crate::operators::groups::GroupTable;
use crate::operators::{eval_cmp_values, eval_comparison, eval_expr, is_true};
use crate::query::AggKind;
use crate::validator::{ROuterTerm, ResolvedQuery};

/// Execute the query via the decorrelated plan.
pub fn execute(q: &ResolvedQuery) -> QResult<Batch> {
    let outer = q.outer_batch.clone();
    let term = q.terms[q.subquery_at].clone();

    // The single subquery carried by the subquery term.
    let sub = match &term {
        ROuterTerm::Exists { sub, .. }
        | ROuterTerm::ScalarSub { sub, .. }
        | ROuterTerm::InSub { sub, .. } => sub.clone(),
        ROuterTerm::Local(_) => {
            return Err(QError::internal(
                "subquery term index points at a local predicate",
            ));
        }
    };

    // Step 1: group the inner relation once (bulk build, no per-outer scan).
    let groups = GroupTable::build(&sub)?;

    // Step 2/3: stream the outer relation, preserving order and multiplicity.
    let mut kept: Vec<usize> = Vec::new();
    for orow in 0..outer.row_count() {
        if !outer_locals_pass(q, &outer, orow)? {
            continue;
        }
        let group = groups.lookup(&outer, orow)?;
        let ok = match &term {
            ROuterTerm::Exists { negated, .. } => eval_exists(*negated, group.is_some()),
            ROuterTerm::ScalarSub {
                outer: oe,
                op,
                sub: rsub,
            } => {
                let value = scalar_for_group(rsub, group.map(|(g, _)| g))?;
                let ov = eval_expr(oe, &outer, orow);
                matches!(eval_cmp_values(*op, &ov, &value)?, Some(true))
            }
            ROuterTerm::InSub { outer: oe, .. } => {
                let ov = eval_expr(oe, &outer, orow);
                let tri = in_for_group(&groups, group.map(|(g, _)| g), &ov)?;
                tri == Some(true)
            }
            ROuterTerm::Local(_) => unreachable!(),
        };
        if ok {
            kept.push(orow);
        }
    }

    project(q, &outer, &kept)
}

fn outer_locals_pass(q: &ResolvedQuery, outer: &Batch, orow: usize) -> QResult<bool> {
    for t in &q.terms {
        if let ROuterTerm::Local(cmp) = t {
            if !is_true(eval_comparison(cmp, outer, orow)?) {
                return Ok(false);
            }
        }
    }
    Ok(true)
}

fn eval_exists(negated: bool, present: bool) -> bool {
    if negated {
        !present
    } else {
        present
    }
}

/// Compute the scalar value for the group an outer row matched.
///
/// Aggregate: aggregate the (already filtered/keyed) group. Bare: enforce
/// cardinality <= 1 and read its single projected value.
fn scalar_for_group(
    sub: &crate::validator::RSubquery,
    group: Option<&crate::operators::groups::GroupData>,
) -> QResult<Scalar> {
    if let Some((func, _)) = &sub.aggregate {
        let mut acc = AggAcc::new(*func);
        if let Some(g) = group {
            for &irow in &g.rows {
                let v = sub
                    .aggregate
                    .as_ref()
                    .map(|(_, c)| sub.inner_batch.get(c.index, irow).clone())
                    .unwrap_or(Scalar::Null);
                acc.update(&v)?;
            }
        }
        // Empty/absent group: COUNT -> 0, SUM -> NULL, handled by the acc.
        return Ok(acc.finish());
    }

    // Bare scalar subquery.
    let proj = sub
        .project
        .as_ref()
        .ok_or_else(|| QError::internal("bare scalar subquery missing projection"))?;
    match group {
        None => Ok(Scalar::Null),
        Some(g) if g.is_empty() => Ok(Scalar::Null),
        Some(g) if g.len() > 1 => Err(QError::new(
            ErrorKind::ScalarMultipleRows,
            "scalar subquery returned more than one row for an outer row",
        )),
        Some(g) => Ok(sub.inner_batch.get(proj.index, g.rows[0]).clone()),
    }
}

/// NULL-aware IN evaluated against the single matched group.
///
/// The group contains exactly the inner rows whose correlation key equals the
/// outer key under `=`. Within it, the projection is matched against the
/// outer value with SQL 3VL.
fn in_for_group(
    groups: &GroupTable<'_>,
    group: Option<&crate::operators::groups::GroupData>,
    ov: &Scalar,
) -> QResult<Option<bool>> {
    if ov.is_null() {
        return Ok(None);
    }
    let Some(g) = group else {
        return Ok(Some(false));
    };
    let mut saw_null = false;
    for &irow in &g.rows {
        let iv = groups.project_value(irow);
        match ov.sql_eq(&iv)? {
            Some(true) => return Ok(Some(true)),
            None => saw_null = true,
            Some(false) => {}
        }
    }
    if saw_null {
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

/// Markers for tracing which aggregate kind a plan uses (kept for trace output).
pub fn agg_name(k: AggKind) -> &'static str {
    match k {
        AggKind::Count => "COUNT",
        AggKind::Sum => "SUM",
    }
}

pub mod proofs;
