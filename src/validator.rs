//! Validation, name resolution and support checks.
//!
//! The validator takes the raw [`Query`] AST plus the populated [`Catalog`]
//! and produces a [`ResolvedQuery`] in which every column reference carries
//! the physical column index in its relation. It also enforces the supported
//! fragment: anything outside it is rejected here, before any rewriting, with
//! a precise [`ErrorKind`].

use std::sync::Arc;

use crate::batch::{Batch, DataType};
use crate::error::{ErrorKind, QError, QResult};
use crate::query::{
    AggKind, CmpOp, ColumnRef, Expr, InnerTerm, OuterTerm, Query, RelationRef, SubForm, Subquery,
};
use crate::resource::Catalog;

/// A column reference resolved to a physical index.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResolvedColumn {
    pub table: String,
    pub column: String,
    pub index: usize,
    pub dtype: DataType,
}

#[derive(Debug, Clone)]
pub enum RExpr {
    Null,
    IntLit(i64),
    StrLit(Arc<str>),
    Column(ResolvedColumn),
}

impl RExpr {
    pub fn dtype(&self) -> Option<DataType> {
        match self {
            RExpr::Null => None,
            RExpr::IntLit(_) => Some(DataType::Int),
            RExpr::StrLit(_) => Some(DataType::Str),
            RExpr::Column(c) => Some(c.dtype),
        }
    }
}

#[derive(Debug, Clone)]
pub struct RComparison {
    pub op: CmpOp,
    pub left: RExpr,
    pub right: RExpr,
}

#[derive(Debug, Clone)]
pub enum RInnerTerm {
    Corr {
        outer: ResolvedColumn,
        inner: ResolvedColumn,
    },
    Local(RComparison),
}

#[derive(Debug, Clone)]
pub struct RSubquery {
    pub relation: String,
    pub inner_batch: Arc<Batch>,
    pub terms: Vec<RInnerTerm>,
    /// Resolved unaggregated projection (IN).
    pub project: Option<ResolvedColumn>,
    /// Resolved aggregate (scalar subqueries).
    pub aggregate: Option<(AggKind, ResolvedColumn)>,
}

#[derive(Debug, Clone)]
pub enum ROuterTerm {
    Local(RComparison),
    Exists {
        sub: RSubquery,
        negated: bool,
    },
    ScalarSub {
        outer: RExpr,
        op: CmpOp,
        sub: RSubquery,
    },
    InSub {
        outer: RExpr,
        sub: RSubquery,
        negated: bool,
    },
}

#[derive(Debug, Clone)]
pub struct RSelectItem {
    pub name: String,
    pub index: usize,
}

#[derive(Debug, Clone)]
pub struct ResolvedQuery {
    pub outer_relation: String,
    pub outer_binding: String,
    pub outer_batch: Arc<Batch>,
    pub select: Vec<RSelectItem>,
    pub terms: Vec<ROuterTerm>,
    /// Index of the single subquery term, and its form.
    pub subquery_at: usize,
    pub form: SubForm,
}

/// Validate and resolve a whole query against the catalog.
pub fn validate(query: &Query, catalog: &Catalog) -> QResult<ResolvedQuery> {
    let outer_batch = catalog.get(&query.from.relation)?;
    let outer_binding = query.from.binding().to_string();

    // SELECT list
    if query.select.is_empty() {
        return Err(QError::invalid_request("outer SELECT list is empty"));
    }
    let mut select = Vec::with_capacity(query.select.len());
    for c in &query.select {
        let rc = resolve_column(c, &query.from, &outer_batch, "outer SELECT")?;
        select.push(RSelectItem {
            name: rc.column.clone(),
            index: rc.index,
        });
    }

    if query.where_terms.is_empty() {
        return Err(QError::unsupported(
            "query has no subquery; this service only compiles subquery-bearing queries",
        ));
    }

    // Exactly one subquery term is permitted.
    let mut subquery_at: Option<(usize, SubForm)> = None;
    for (i, t) in query.where_terms.iter().enumerate() {
        let form = match t {
            OuterTerm::Local { .. } => continue,
            OuterTerm::Exists { negated: false, .. } => SubForm::Exists,
            OuterTerm::Exists { negated: true, .. } => SubForm::NotExists,
            OuterTerm::ScalarSub { sub, .. } => {
                if sub.aggregate.is_some() {
                    SubForm::ScalarAgg
                } else {
                    SubForm::ScalarBare
                }
            }
            OuterTerm::InSub { .. } => SubForm::InUnaggregated,
        };
        if subquery_at.is_some() {
            return Err(QError::unsupported(
                "more than one subquery predicate in the outer WHERE; only one is supported",
            ));
        }
        subquery_at = Some((i, form));
    }
    let (subquery_at, form) = subquery_at.ok_or_else(|| {
        QError::unsupported("no subquery found; the service requires exactly one subquery")
    })?;

    let mut terms = Vec::with_capacity(query.where_terms.len());
    for (i, term) in query.where_terms.iter().enumerate() {
        terms.push(resolve_outer_term(
            term,
            &query.from,
            &outer_batch,
            catalog,
            i == subquery_at,
            form,
        )?);
    }

    // Cross-check the classified term against resolved shape.
    validate_form_shapes(&terms[subquery_at], form)?;

    Ok(ResolvedQuery {
        outer_relation: query.from.relation.clone(),
        outer_binding,
        outer_batch,
        select,
        terms,
        subquery_at,
        form,
    })
}

fn resolve_outer_term(
    term: &OuterTerm,
    outer_rel: &RelationRef,
    outer_batch: &Arc<Batch>,
    catalog: &Catalog,
    is_sub_term: bool,
    form: SubForm,
) -> QResult<ROuterTerm> {
    let _ = (is_sub_term, form);
    match term {
        OuterTerm::Local { op, left, right } => {
            let l = resolve_expr(left, outer_rel, outer_batch)?;
            let r = resolve_expr(right, outer_rel, outer_batch)?;
            comparison_type_check(*op, &l, &r)?;
            Ok(ROuterTerm::Local(RComparison {
                op: *op,
                left: l,
                right: r,
            }))
        }
        OuterTerm::Exists { sub, negated } => {
            let rsub =
                resolve_subquery_cat(sub, outer_rel, outer_batch, catalog, SubPurpose::Exists)?;
            Ok(ROuterTerm::Exists {
                sub: rsub,
                negated: *negated,
            })
        }
        OuterTerm::ScalarSub { outer, op, sub } => {
            let outer_expr = resolve_expr(outer, outer_rel, outer_batch)?;
            let purpose = if sub.aggregate.is_some() {
                SubPurpose::ScalarAgg
            } else {
                SubPurpose::ScalarBare
            };
            let rsub = resolve_subquery_cat(sub, outer_rel, outer_batch, catalog, purpose)?;
            scalar_comparison_types(&outer_expr, &rsub, *op)?;
            Ok(ROuterTerm::ScalarSub {
                outer: outer_expr,
                op: *op,
                sub: rsub,
            })
        }
        OuterTerm::InSub {
            outer,
            sub,
            negated,
        } => {
            if *negated {
                return Err(QError::unsupported(
                    "NOT IN (subquery) is not supported: its NULL semantics (UNKNOWN whenever the \
                     subquery yields NULL, trap at the IN-list level) differ from NOT EXISTS; use \
                     NOT EXISTS with an explicit null-rejecting predicate",
                ));
            }
            let outer_expr = resolve_expr(outer, outer_rel, outer_batch)?;
            let rsub = resolve_subquery_cat(sub, outer_rel, outer_batch, catalog, SubPurpose::In)?;
            in_type_check(&outer_expr, &rsub)?;
            Ok(ROuterTerm::InSub {
                outer: outer_expr,
                sub: rsub,
                negated: false,
            })
        }
    }
}

#[derive(Clone, Copy)]
enum SubPurpose {
    Exists,
    ScalarAgg,
    ScalarBare,
    In,
}

/// Real subquery resolution with catalog access.
fn resolve_subquery_cat(
    sub: &Subquery,
    outer_rel: &RelationRef,
    outer_batch: &Arc<Batch>,
    catalog: &Catalog,
    purpose: SubPurpose,
) -> QResult<RSubquery> {
    let inner_batch = catalog.get(&sub.from.relation)?;
    let inner_binding = sub.from.binding();
    let outer_binding = outer_rel.binding();

    if sub.from.relation == outer_rel.relation && inner_binding == outer_binding {
        // Self correlation via distinct alias would be needed; same binding on
        // both sides is ambiguous.
        return Err(QError::new(
            ErrorKind::CorrelationMismatch,
            "inner and outer relation use the same binding; give the inner relation a distinct alias",
        ));
    }

    // Shape checks for the projection.
    let project = match purpose {
        SubPurpose::Exists => {
            if sub.project.is_some() {
                return Err(QError::unsupported(
                    "EXISTS subquery must not project a column",
                ));
            }
            if sub.aggregate.is_some() {
                return Err(QError::unsupported(
                    "EXISTS subquery must not use an aggregate",
                ));
            }
            None
        }
        SubPurpose::ScalarAgg => {
            if sub.project.is_some() {
                return Err(QError::unsupported(
                    "scalar subquery cannot mix a bare projection with an aggregate",
                ));
            }
            None
        }
        SubPurpose::ScalarBare => {
            if sub.project.is_none() {
                return Err(QError::invalid_request(
                    "scalar subquery must project one column or contain one aggregate",
                ));
            }
            let p = sub.project.as_ref().unwrap();
            Some(resolve_column(
                p,
                &sub.from,
                &inner_batch,
                "scalar subquery projection",
            )?)
        }
        SubPurpose::In => {
            if sub.aggregate.is_some() {
                return Err(QError::unsupported(
                    "IN subquery must project a bare column, not an aggregate",
                ));
            }
            let p = sub.project.as_ref().ok_or_else(|| {
                QError::invalid_request("IN subquery is missing its projected column")
            })?;
            Some(resolve_column(
                p,
                &sub.from,
                &inner_batch,
                "IN subquery projection",
            )?)
        }
    };

    let aggregate = match (purpose, &sub.aggregate) {
        (SubPurpose::ScalarAgg, Some(a)) => {
            let col = resolve_column(&a.column, &sub.from, &inner_batch, "aggregate argument")?;
            if matches!(a.func, AggKind::Sum) && col.dtype != DataType::Int {
                return Err(QError::typemsg(format!(
                    "SUM argument must be int, got {}",
                    col.dtype.name()
                )));
            }
            Some((a.func, col))
        }
        (SubPurpose::ScalarAgg, None) => {
            return Err(QError::invalid_request(
                "scalar comparison subquery must contain exactly one COUNT/SUM aggregate",
            ));
        }
        _ => None,
    };

    let mut has_corr = false;
    let mut corr_pairs: Vec<(String, String)> = Vec::new();
    let mut terms = Vec::with_capacity(sub.where_terms.len());
    for t in &sub.where_terms {
        match t {
            InnerTerm::Correlated(c) => {
                let (l_side, r_side) = (&c.left, &c.right);
                let l_bind = l_side.table.as_deref();
                let r_bind = r_side.table.as_deref();
                let (outer_ref, inner_ref) = orient_correlation(
                    l_side,
                    r_side,
                    l_bind,
                    r_bind,
                    outer_binding,
                    inner_binding,
                )?;
                let outer_col =
                    resolve_column(&outer_ref, outer_rel, outer_batch, "correlation outer side")?;
                let inner_col = resolve_column(
                    &inner_ref,
                    &sub.from,
                    &inner_batch,
                    "correlation inner side",
                )?;
                if outer_col.dtype != inner_col.dtype {
                    return Err(QError::new(
                        ErrorKind::CorrelationMismatch,
                        format!(
                            "correlation {}.{} = {}.{} joins {} with {}",
                            outer_col.table,
                            outer_col.column,
                            inner_col.table,
                            inner_col.column,
                            outer_col.dtype.name(),
                            inner_col.dtype.name()
                        ),
                    ));
                }
                has_corr = true;
                corr_pairs.push((outer_col.column.clone(), inner_col.column.clone()));
                terms.push(RInnerTerm::Corr {
                    outer: outer_col,
                    inner: inner_col,
                });
            }
            InnerTerm::Local { op, left, right } => {
                let l = resolve_expr(left, &sub.from, &inner_batch)?;
                let r = resolve_expr(right, &sub.from, &inner_batch)?;
                comparison_type_check(*op, &l, &r)?;
                terms.push(RInnerTerm::Local(RComparison {
                    op: *op,
                    left: l,
                    right: r,
                }));
            }
        }
    }

    if !has_corr {
        return Err(QError::new(
            ErrorKind::CorrelationMismatch,
            "subquery is not correlated: at least one outer.col = inner.col predicate is required \
             (uncorrelated subqueries are outside this service's fragment)",
        ));
    }

    // Correlation must be on distinct column pairs aligned by type (checked),
    // and the same inner column must not be equated to two different outer
    // columns in a way that would make the join key ambiguous for grouping.
    if corr_pairs.len()
        != corr_pairs
            .iter()
            .collect::<std::collections::HashSet<_>>()
            .len()
    {
        return Err(QError::new(
            ErrorKind::CorrelationMismatch,
            "duplicate correlation predicates on the same column pair",
        ));
    }

    Ok(RSubquery {
        relation: sub.from.relation.clone(),
        inner_batch,
        terms,
        project,
        aggregate,
    })
}

fn orient_correlation(
    l: &ColumnRef,
    r: &ColumnRef,
    l_bind: Option<&str>,
    r_bind: Option<&str>,
    outer_binding: &str,
    inner_binding: &str,
) -> QResult<(ColumnRef, ColumnRef)> {
    let l_outer = l_bind == Some(outer_binding);
    let l_inner = l_bind == Some(inner_binding);
    let r_outer = r_bind == Some(outer_binding);
    let r_inner = r_bind == Some(inner_binding);
    match (l_outer, l_inner, r_outer, r_inner) {
        (true, false, false, true) => Ok((l.clone(), r.clone())),
        (false, true, true, false) => Ok((r.clone(), l.clone())),
        _ => Err(QError::new(
            ErrorKind::CorrelationMismatch,
            format!(
                "correlation must be {outer_binding}.col = {inner_binding}.col; got sides '{}'/'{}'",
                l_bind.unwrap_or("?"),
                r_bind.unwrap_or("?")
            ),
        )),
    }
}

fn resolve_column(
    c: &ColumnRef,
    rel: &RelationRef,
    batch: &Arc<Batch>,
    ctx: &str,
) -> QResult<ResolvedColumn> {
    let binding = rel.binding();
    if let Some(t) = &c.table {
        if t != binding && t != &rel.relation {
            return Err(QError::new(
                ErrorKind::UnknownReference,
                format!("{ctx}: table qualifier '{t}' does not match binding '{binding}'"),
            ));
        }
    }
    let index = batch
        .schema()
        .index_of(&c.column)
        .map_err(|e| QError::new(ErrorKind::UnknownReference, format!("{ctx}: {}", e.message)))?;
    Ok(ResolvedColumn {
        table: binding.to_string(),
        column: c.column.clone(),
        index,
        dtype: batch.schema().fields[index].dtype,
    })
}

fn resolve_expr(e: &Expr, rel: &RelationRef, batch: &Arc<Batch>) -> QResult<RExpr> {
    match e {
        Expr::Literal { dtype, value } => match (dtype, value) {
            (crate::query::LiteralType::Int, Some(s)) => {
                let i: i64 = s.parse().map_err(|_| {
                    QError::new(
                        ErrorKind::InvalidLiteral,
                        format!("invalid int literal '{s}'"),
                    )
                })?;
                Ok(RExpr::IntLit(i))
            }
            (crate::query::LiteralType::Int, None) => Ok(RExpr::Null),
            (crate::query::LiteralType::Str, Some(s)) => Ok(RExpr::StrLit(Arc::from(s.as_str()))),
            (crate::query::LiteralType::Str, None) => Ok(RExpr::Null),
        },
        Expr::Column(c) => Ok(RExpr::Column(resolve_column(c, rel, batch, "expression")?)),
    }
}

fn comparison_type_check(op: CmpOp, l: &RExpr, r: &RExpr) -> QResult<()> {
    let lt = l.dtype();
    let rt = r.dtype();
    match (lt, rt) {
        (None, _) | (_, None) => {} // NULL literal comparable with anything (UNKNOWN at runtime)
        (Some(a), Some(b)) if a == b => {}
        (Some(a), Some(b)) => {
            return Err(QError::typemsg(format!(
                "{op:?} compares {} with {}",
                a.name(),
                b.name()
            )));
        }
    }
    Ok(())
}

fn scalar_comparison_types(outer: &RExpr, sub: &RSubquery, op: CmpOp) -> QResult<()> {
    // COUNT always yields int; SUM yields the argument type (int here);
    // a bare scalar subquery yields its projection type.
    let sub_dt = match &sub.aggregate {
        Some((AggKind::Count, _)) => DataType::Int,
        Some((AggKind::Sum, c)) => c.dtype,
        None => {
            sub.project
                .as_ref()
                .ok_or_else(|| {
                    QError::internal("scalar subquery has neither aggregate nor projection")
                })?
                .dtype
        }
    };
    let outer_dt = outer.dtype();
    if let Some(o) = outer_dt {
        if o != sub_dt {
            return Err(QError::typemsg(format!(
                "scalar comparison {op:?} compares outer {} with subquery {}",
                o.name(),
                sub_dt.name()
            )));
        }
    }
    Ok(())
}

fn in_type_check(outer: &RExpr, sub: &RSubquery) -> QResult<()> {
    let p = sub
        .project
        .as_ref()
        .ok_or_else(|| QError::internal("IN subquery resolved without projection"))?;
    if let Some(o) = outer.dtype() {
        if o != p.dtype {
            return Err(QError::typemsg(format!(
                "IN compares outer {} with subquery {}",
                o.name(),
                p.dtype.name()
            )));
        }
    }
    Ok(())
}

fn validate_form_shapes(term: &ROuterTerm, form: SubForm) -> QResult<()> {
    match term {
        ROuterTerm::Exists { negated: false, .. } if form == SubForm::Exists => Ok(()),
        ROuterTerm::Exists { negated: true, .. } if form == SubForm::NotExists => Ok(()),
        ROuterTerm::ScalarSub { sub, .. }
            if form == SubForm::ScalarAgg && sub.aggregate.is_some() =>
        {
            Ok(())
        }
        ROuterTerm::ScalarSub { sub, .. }
            if form == SubForm::ScalarBare && sub.aggregate.is_none() =>
        {
            Ok(())
        }
        ROuterTerm::InSub { negated: false, .. } if form == SubForm::InUnaggregated => Ok(()),
        _ => Err(QError::internal(
            "classified subquery term does not match its form",
        )),
    }
}
