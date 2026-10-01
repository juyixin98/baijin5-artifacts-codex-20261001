//! Query AST for the supported SQL fragment.
//!
//! The AST is deliberately small. It is parsed from the JSON request in
//! [`crate::api`] and validated / named-resolved by [`crate::validator`].
//!
//! Supported shapes:
//!
//! * outer `SELECT ... FROM <rel> WHERE <predicate with at most one subquery>`
//! * subquery `EXISTS ( correlated SELECT ... )`
//! * subquery `NOT EXISTS ( correlated SELECT ... )`
//! * scalar comparison `<expr> <cmp> ( correlated SELECT <agg>(...) ... )`
//! * `expr IN ( correlated unaggregated SELECT ... )`  (NULL-aware)
//!
//! Explicitly **not** supported and rejected with [`ErrorKind::UnsupportedForm`]:
//! `NOT IN`, joins, set ops, nested subqueries, non-equality correlation,
//! HAVING / ORDER BY / LIMIT, multiple subqueries, subqueries in the SELECT
//! list.
//!
//! [`ErrorKind::UnsupportedForm`]: crate::error::ErrorKind::UnsupportedForm

use serde::{Deserialize, Serialize};

use crate::batch::DataType;

/// A relation reference: the catalog name and an alias.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RelationRef {
    pub relation: String,
    #[serde(default)]
    pub alias: Option<String>,
}

impl RelationRef {
    pub fn binding(&self) -> &str {
        self.alias.as_deref().unwrap_or(&self.relation)
    }
}

/// An equality correlation predicate `outer.col = inner.col` (direction-agnostic).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CorrEq {
    pub left: ColumnRef,
    pub right: ColumnRef,
}

/// A qualified or unqualified column reference.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct ColumnRef {
    #[serde(default)]
    pub table: Option<String>,
    pub column: String,
}

impl ColumnRef {
    pub fn bare(column: impl Into<String>) -> Self {
        Self {
            table: None,
            column: column.into(),
        }
    }
    pub fn qualified(table: impl Into<String>, column: impl Into<String>) -> Self {
        Self {
            table: Some(table.into()),
            column: column.into(),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum CmpOp {
    Eq,
    Ne,
    Lt,
    Le,
    Gt,
    Ge,
}

impl CmpOp {
    pub fn eval(self, a: i64, b: i64) -> bool {
        match self {
            CmpOp::Eq => a == b,
            CmpOp::Ne => a != b,
            CmpOp::Lt => a < b,
            CmpOp::Le => a <= b,
            CmpOp::Gt => a > b,
            CmpOp::Ge => a >= b,
        }
    }
}

/// Scalar expressions (no subqueries here).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Expr {
    Literal {
        #[serde(rename = "type")]
        dtype: LiteralType,
        value: Option<String>,
    },
    Column(ColumnRef),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum LiteralType {
    Int,
    Str,
}

impl LiteralType {
    pub fn dtype(self) -> DataType {
        match self {
            LiteralType::Int => DataType::Int,
            LiteralType::Str => DataType::Str,
        }
    }
}

/// WHERE predicates are modeled per-block as flat ANDed term lists
/// ([`Query::where_terms`] / [`Subquery::where_terms`]); exactly one outer
/// term may be a subquery term. See [`OuterTerm`] and [`InnerTerm`].

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum AggKind {
    Count,
    Sum,
}

/// The single inner SELECT. Projection is either a bare column (unaggregated,
/// for EXISTS / IN) or one aggregate over a column (scalar subqueries).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Subquery {
    pub from: RelationRef,
    /// Correlation equalities and any local inner predicates, combined by AND.
    /// Stored as a flat list; entries are tagged.
    #[serde(default)]
    pub where_terms: Vec<InnerTerm>,
    /// Unaggregated projection column. Required for `IN`, must be absent for
    /// `EXISTS` and for aggregate scalar subqueries.
    #[serde(default)]
    pub project: Option<ColumnRef>,
    /// `None` => unaggregated (EXISTS / IN); `Some` => one aggregate (scalar).
    #[serde(default)]
    pub aggregate: Option<AggProj>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AggProj {
    pub func: AggKind,
    pub column: ColumnRef,
}

/// One ANDed term inside the inner WHERE clause.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum InnerTerm {
    /// `outer.col = inner.col`
    Correlated(CorrEq),
    /// a comparison referencing only the inner relation
    Local { op: CmpOp, left: Expr, right: Expr },
}

/// Resolved marker for the local-predicate shape (documentation/helpers).
pub type LocalPredShape = (CmpOp, Expr, Expr);

/// The outer query block.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Query {
    pub select: Vec<ColumnRef>,
    pub from: RelationRef,
    /// Flat ANDed list of outer terms; exactly one may be a subquery term.
    #[serde(default)]
    pub where_terms: Vec<OuterTerm>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum OuterTerm {
    /// Outer-only comparison.
    Local { op: CmpOp, left: Expr, right: Expr },
    /// A standalone `EXISTS` / `NOT EXISTS` predicate.
    Exists { sub: Box<Subquery>, negated: bool },
    /// Scalar aggregate comparison.
    ScalarSub {
        outer: Expr,
        op: CmpOp,
        sub: Box<Subquery>,
    },
    /// IN subquery (only positive `IN` is accepted; `negated: true` -> reject).
    InSub {
        outer: Expr,
        sub: Box<Subquery>,
        #[serde(default)]
        negated: bool,
    },
}

/// Which of the supported subquery forms a query contains.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SubForm {
    Exists,
    NotExists,
    /// Scalar comparison against an aggregate subquery.
    ScalarAgg,
    /// Scalar comparison against a bare (non-aggregate) subquery; cardinality
    /// must be at most one per outer row.
    ScalarBare,
    InUnaggregated,
}

impl Query {
    /// Find the single subquery term and classify the query.
    pub fn subquery_form(&self) -> Option<(SubForm, usize)> {
        for (i, t) in self.where_terms.iter().enumerate() {
            match t {
                OuterTerm::Exists { negated: false, .. } => return Some((SubForm::Exists, i)),
                OuterTerm::Exists { negated: true, .. } => return Some((SubForm::NotExists, i)),
                OuterTerm::ScalarSub { sub, .. } => {
                    let form = if sub.aggregate.is_some() {
                        SubForm::ScalarAgg
                    } else {
                        SubForm::ScalarBare
                    };
                    return Some((form, i));
                }
                OuterTerm::InSub { .. } => return Some((SubForm::InUnaggregated, i)),
                OuterTerm::Local { .. } => {}
            }
        }
        None
    }
}

/// Convenience constructor used by tests and fixtures.
pub fn corr(outer: (&str, &str), inner: (&str, &str)) -> InnerTerm {
    InnerTerm::Correlated(CorrEq {
        left: ColumnRef::qualified(outer.0, outer.1),
        right: ColumnRef::qualified(inner.0, inner.1),
    })
}
