//! Validation entry point.
//!
//! This module is the single gate between wire requests and execution:
//!
//! 1. Recognized-but-rejected forms (`NOT IN`, `IN`, `ANY`, `ALL`) fail here
//!    with [`FailureCategory::UnsupportedForm`], carrying an explicit
//!    explanation that `NOT IN` NULL semantics are not equivalent to
//!    `NOT EXISTS`. This is a deliberate refusal, not an oversight.
//! 2. Structural and schema checks produce a typed [`QueryPlan`].
//! 3. The [`RewriteProof`] for the accepted plan is assembled here, stating the
//!    algebraic identity applied and the hazards that were checked.

use crate::ast::{QueryRequest, RewriteProof, SubquerySpec};
use crate::batch::LogicalType;
use crate::catalog::Catalog;
use crate::error::{EngineError, EngineResult};
use crate::plan::{AggKind, CorrelationEq, QueryPlan, SubqueryKind, SubqueryNode};

/// Rejected operator spellings -> why they cannot be silently rewritten.
fn rejected_operator(op: &str) -> Option<(String, String, String)> {
    match op {
        "not_in" => Some((
            "NOT IN".to_string(),
            "`x NOT IN (SELECT y ...)` is NOT the anti-join of `NOT EXISTS`: \
             if the inner list contains any NULL, `NOT IN` evaluates to UNKNOWN for every \
             outer row (so the outer row is dropped), whereas `NOT EXISTS` keeps rows with no \
             matching inner record. The decorrelation identity for anti-join therefore does not \
             apply, and rewriting one into the other silently changes results under NULLs."
                .to_string(),
            "rewrite the query explicitly as `NOT EXISTS (SELECT 1 FROM inner WHERE inner.y = outer.x)`, \
             or supply an inner list guaranteed NULL-free (then submit op=`not_exists`)."
                .to_string(),
        )),
        "in" => Some((
            "IN".to_string(),
            "semi-join `IN` with a nullable inner list has its own three-valued logic and \
             duplicate/NULL edge cases; this service only compiles the explicit `EXISTS` form."
                .to_string(),
            "use op=`exists` with the equivalent correlated equality.".to_string(),
        )),
        "any" => Some(rejected_quantified("ANY")),
        "all" => Some(rejected_quantified("ALL")),
        _ => None,
    }
}

fn rejected_quantified(form: &str) -> (String, String, String) {
    (
        form.to_string(),
        format!(
            "quantified comparison subqueries ({form}) have NULL-aware semantics distinct from the \
             EXISTS family and are not part of the supported decorrelation surface."
        ),
        "reformulate using an EXISTS / NOT EXISTS correlated subquery.".to_string(),
    )
}

pub fn validate(req: &QueryRequest, catalog: &Catalog) -> EngineResult<QueryPlan> {
    let outer_rel = catalog
        .get(&req.outer.relation)
        .map_err(|e| locate(e, "outer.relation"))?;
    let inner_rel = catalog
        .get(&req.subquery.inner_relation)
        .map_err(|e| locate(e, "subquery.inner_relation"))?;

    reject_unsupported(&req.subquery)?;

    // Outer projection.
    let outer_select: Vec<String> = if req.outer.select.is_empty() {
        outer_rel.schema.iter().map(|(n, _)| n.clone()).collect()
    } else {
        for name in &req.outer.select {
            if !outer_rel.schema.iter().any(|(n, _)| n == name) {
                return Err(EngineError::schema(format!(
                    "outer relation `{}` has no column `{name}`",
                    outer_rel.name
                )));
            }
        }
        req.outer.select.clone()
    };

    // Output column must not collide with a projected outer column.
    if outer_select
        .iter()
        .any(|n| n == &req.subquery.output_column)
    {
        return Err(EngineError::validation(
            format!(
                "subquery output column `{}` collides with an outer projection column",
                req.subquery.output_column
            ),
            "subquery.output_column",
        ));
    }
    if req.subquery.output_column.trim().is_empty() {
        return Err(EngineError::validation(
            "subquery output_column must be non-empty".to_string(),
            "subquery.output_column",
        ));
    }

    // Correlation: at least one conjunct; columns must exist and types match.
    if req.subquery.correlation.is_empty() {
        return Err(EngineError::validation(
            "an uncorrelated subquery has nothing to decorrelate; provide at least one \
             `inner.col = outer.col` correlation conjunct"
                .to_string(),
            "subquery.correlation",
        ));
    }
    let mut correlation = Vec::with_capacity(req.subquery.correlation.len());
    for (i, c) in req.subquery.correlation.iter().enumerate() {
        let ot = outer_rel
            .schema
            .iter()
            .find(|(n, _)| n == &c.outer)
            .map(|(_, t)| *t)
            .ok_or_else(|| {
                EngineError::schema(format!(
                    "correlation[{i}]: outer relation `{}` has no column `{}`",
                    outer_rel.name, c.outer
                ))
            })?;
        let it = inner_rel
            .schema
            .iter()
            .find(|(n, _)| n == &c.inner)
            .map(|(_, t)| *t)
            .ok_or_else(|| {
                EngineError::schema(format!(
                    "correlation[{i}]: inner relation `{}` has no column `{}`",
                    inner_rel.name, c.inner
                ))
            })?;
        if ot != it {
            return Err(EngineError::Schema {
                message: format!(
                    "correlation[{i}] type mismatch: outer.{} is {} but inner.{} is {}",
                    c.outer,
                    ot.name(),
                    c.inner,
                    it.name()
                ),
            });
        }
        correlation.push(CorrelationEq {
            outer_column: c.outer.clone(),
            inner_column: c.inner.clone(),
        });
    }

    let kind = compile_kind(&req.subquery, &inner_rel.schema)?;

    Ok(QueryPlan {
        outer_relation: outer_rel.name,
        outer_select,
        subquery: SubqueryNode {
            inner_relation: inner_rel.name,
            correlation,
            kind,
            output_column: req.subquery.output_column.clone(),
        },
    })
}

fn reject_unsupported(sq: &SubquerySpec) -> EngineResult<()> {
    if let Some((form, reason, remediation)) = rejected_operator(&sq.op) {
        return Err(EngineError::Unsupported {
            form: form.to_string(),
            location: "subquery.op".to_string(),
            reason: reason.to_string(),
            remediation: remediation.to_string(),
        });
    }
    Ok(())
}

fn compile_kind(
    sq: &SubquerySpec,
    inner_schema: &[(String, LogicalType)],
) -> EngineResult<SubqueryKind> {
    let find_inner = |name: &str| -> EngineResult<LogicalType> {
        inner_schema
            .iter()
            .find(|(n, _)| n == name)
            .map(|(_, t)| *t)
            .ok_or_else(|| EngineError::schema(format!("inner relation has no column `{name}`")))
    };

    match sq.op.as_str() {
        "exists" => require_no_agg(sq, "exists").map(|()| SubqueryKind::Exists),
        "not_exists" => require_no_agg(sq, "not_exists").map(|()| SubqueryKind::NotExists),
        "scalar" => {
            // scalar takes no aggregate, but DOES require value_column.
            if sq.aggregate.is_some() {
                return Err(EngineError::validation(
                    "`scalar` does not take `aggregate`".to_string(),
                    "subquery.aggregate",
                ));
            }
            let value_column = sq.value_column.clone().ok_or_else(|| {
                EngineError::validation(
                    "scalar subquery requires `value_column` naming the projected inner column"
                        .to_string(),
                    "subquery.value_column",
                )
            })?;
            find_inner(&value_column)?;
            Ok(SubqueryKind::Scalar { value_column })
        }
        "scalar_aggregate" => {
            let agg_name = sq.aggregate.as_deref().ok_or_else(|| {
                EngineError::validation(
                    "scalar_aggregate requires `aggregate` of `count_star` or `sum`".to_string(),
                    "subquery.aggregate",
                )
            })?;
            match agg_name {
                "count_star" => {
                    if sq.value_column.is_some() {
                        return Err(EngineError::validation(
                            "count_star takes no value_column".to_string(),
                            "subquery.value_column",
                        ));
                    }
                    Ok(SubqueryKind::ScalarAggregate {
                        agg: AggKind::Count,
                        value_column: None,
                    })
                }
                "sum" => {
                    let value_column = sq.value_column.clone().ok_or_else(|| {
                        EngineError::validation(
                            "SUM aggregate requires `value_column`".to_string(),
                            "subquery.value_column",
                        )
                    })?;
                    let ty = find_inner(&value_column)?;
                    if ty != LogicalType::Integer {
                        return Err(EngineError::validation(
                            format!(
                                "SUM is supported on INTEGER columns only; `{value_column}` is {}",
                                ty.name()
                            ),
                            "subquery.value_column",
                        ));
                    }
                    Ok(SubqueryKind::ScalarAggregate {
                        agg: AggKind::Sum,
                        value_column: Some(value_column),
                    })
                }
                other => Err(EngineError::Unsupported {
                    form: format!("aggregate `{other}`"),
                    location: "subquery.aggregate".to_string(),
                    reason: "only COUNT(*) and SUM are in the supported decorrelation surface"
                        .to_string(),
                    remediation: "use count_star or sum".to_string(),
                }),
            }
        }
        other => Err(EngineError::validation(
            format!("unknown subquery op `{other}`"),
            "subquery.op",
        )),
    }
}

fn require_no_agg(sq: &SubquerySpec, op: &str) -> EngineResult<()> {
    if sq.aggregate.is_some() || sq.value_column.is_some() {
        return Err(EngineError::validation(
            format!("`{op}` takes neither `aggregate` nor `value_column`"),
            "subquery",
        ));
    }
    Ok(())
}

fn locate(err: EngineError, location: &str) -> EngineError {
    match err {
        EngineError::Schema { message } => EngineError::Schema {
            message: format!("{location}: {message}"),
        },
        other => other,
    }
}

/// Assemble the applicability proof for the magic-set / group-probe rewrite.
pub fn rewrite_proof(plan: &QueryPlan) -> RewriteProof {
    let sq = &plan.subquery;
    let keys: Vec<String> = sq
        .correlation
        .iter()
        .map(|c| format!("inner.{} = outer.{}", c.inner_column, c.outer_column))
        .collect();

    let (identity, mut argument) = match &sq.kind {
        SubqueryKind::Exists => (
            "EXISTS with correlation predicates C is a dependent semi-join; by the magic-set / \
             group-probe identity it equals a LEFT SEMI JOIN of the outer relation against the \
             inner relation GROUPed BY the correlation key: EXISTS iff the key group is non-empty.",
            vec![
                format!("group key = ({})", keys.join(" AND ")),
                "SQL key equality is used for grouping/probing: an outer NULL key matches no group \
                 (NULL = x is UNKNOWN), exactly as in nested-loop evaluation."
                    .to_string(),
                "outer rows are never removed or collapsed: each outer row emits exactly one result \
                 row, so duplicate outer rows survive with duplicate results."
                    .to_string(),
            ],
        ),
        SubqueryKind::NotExists => (
            "NOT EXISTS with correlation predicates C is a dependent anti-join; it equals a LEFT \
             ANTI JOIN against the inner relation GROUPed BY the correlation key: NOT EXISTS iff \
             the key group is empty (or absent).",
            vec![
                format!("group key = ({})", keys.join(" AND ")),
                "an outer NULL key finds no matching group under SQL equality and therefore yields \
                 TRUE (the anti-join keeps the row), matching per-row interpretation."
                    .to_string(),
                "this identity applies to NOT EXISTS only — NOT IN has different NULL semantics and \
                 was rejected before planning."
                    .to_string(),
                "duplicate outer rows are retained: anti-join is defined per outer row, not per \
                 distinct outer key."
                    .to_string(),
            ],
        ),
        SubqueryKind::Scalar { value_column } => (
            "a scalar subquery is a correlated lookup; grouping the inner by the correlation key and \
             probing per outer row reproduces the nested-loop semantics, provided each matched group \
             has cardinality <= 1.",
            vec![
                format!("group key = ({})", keys.join(" AND ")),
                format!("0 matching rows -> NULL; 1 matching row -> that row's `{value_column}`; \
                         >1 matching rows -> cardinality violation, raised per offending outer row."),
                "the >1-row error is emitted for the same outer row index by both executors."
                    .to_string(),
            ],
        ),
        SubqueryKind::ScalarAggregate { agg, value_column } => match agg {
            AggKind::Count => (
                "COUNT(*) over a correlated group equals the cardinality of the inner group after \
                 GROUP BY the correlation key; empty groups read 0.",
                vec![
                    format!("group key = ({})", keys.join(" AND ")),
                    "COUNT(*) returns 0 for an empty correlated group (no match), never NULL."
                        .to_string(),
                    "NULL keys form their own group in the inner but are never probed by an outer \
                     NULL (SQL equality), so outer NULL reads 0 — identical to per-row evaluation."
                        .to_string(),
                ],
            ),
            AggKind::Sum => (
                "SUM(col) over a correlated group equals the SUM of the inner group after GROUP BY \
                 the correlation key; unlike COUNT, an empty group yields NULL, and all-NULL values \
                 are ignored by SUM.",
                vec![
                    format!("group key = ({})", keys.join(" AND ")),
                    format!("SUM over `{}` ignores NULL inputs; an empty correlated group returns \
                             NULL, whereas COUNT(*) on the same group returns 0 — this distinction is \
                             asserted by the test suite.",
                            value_column.as_deref().unwrap_or("?")),
                    "arithmetic overflow is treated as an execution error by both executors."
                        .to_string(),
                ],
            ),
        },
    };

    argument.push(
        "the inner relation is scanned once and grouped once; the outer relation is \
                   scanned once and probes the groups — this is the decorrelated plan, with no \
                   re-execution of the subquery per outer row."
            .to_string(),
    );

    RewriteProof {
        identity: identity.to_string(),
        argument,
        hazards_checked: vec![
            "NOT IN rejected before planning (NULL-list semantics ≠ NOT EXISTS)".to_string(),
            "correlation columns exist on both sides and have matching types".to_string(),
            "SQL three-valued equality used for keys (NULL never joins)".to_string(),
            "duplicate outer rows preserved (one output row per outer row)".to_string(),
            "empty correlated group: COUNT(*) -> 0, SUM/scalar -> NULL".to_string(),
            "scalar multi-row group -> cardinality violation (per outer row)".to_string(),
        ],
        not_claimed: vec![
            "no support for uncorrelated subqueries, arbitrary predicates, IN/NOT IN, ANY/ALL, \
             AVG/MIN/MAX, or correlated columns in SELECT beyond the value column."
                .to_string(),
            "equivalence is verified empirically against an independent row-by-row interpreter for \
             the submitted data only; the proof text states the algebraic identity, which is \
             general, but this service executes no formal proof checker."
                .to_string(),
        ],
    }
}
