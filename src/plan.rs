//! Internal query plan IR.
//!
//! The supported shape is the classic decorrelation pattern:
//!
//! ```sql
//! SELECT o.*,
//!        <SUBQUERY>(SELECT 1 FROM <inner>
//!                  WHERE <inner>.k = <outer>.k [AND ...]) AS alias
//! FROM   <outer> o
//! ```
//!
//! where `<SUBQUERY>` is one of `EXISTS`, `NOT EXISTS`, a raw scalar subquery,
//! or a scalar aggregate (`COUNT(*)`, `SUM(col)`). Anything else (notably
//! `NOT IN`) is rejected by the validator before planning.

use crate::batch::LogicalType;

/// One correlated equality conjunct: `inner.col = outer.col`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CorrelationEq {
    pub outer_column: String,
    pub inner_column: String,
}

/// Aggregate currently accepted inside a scalar aggregate subquery.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AggKind {
    /// `COUNT(*)`: cardinality of the correlated group; empty group -> 0.
    Count,
    /// `SUM(col)`: SQL sum; empty (or all-NULL) group -> NULL.
    Sum,
}

impl AggKind {
    pub fn name(&self) -> &'static str {
        match self {
            AggKind::Count => "COUNT(*)",
            AggKind::Sum => "SUM",
        }
    }
}

/// The four supported subquery shapes plus the marker for rejected forms.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SubqueryKind {
    Exists,
    NotExists,
    /// `(SELECT col FROM inner WHERE ...)`: 0 rows -> NULL, 1 row -> value,
    /// >1 rows -> cardinality violation.
    Scalar {
        value_column: String,
    },
    ScalarAggregate {
        agg: AggKind,
        value_column: Option<String>,
    },
}

impl SubqueryKind {
    pub fn output_type(&self, inner_value_type: LogicalType) -> LogicalType {
        match self {
            SubqueryKind::Exists | SubqueryKind::NotExists => LogicalType::Boolean,
            SubqueryKind::Scalar { .. } => inner_value_type,
            SubqueryKind::ScalarAggregate { agg, .. } => match agg {
                AggKind::Count => LogicalType::Integer,
                AggKind::Sum => inner_value_type,
            },
        }
    }
}

/// The validated, planner-ready subquery node.
#[derive(Debug, Clone)]
pub struct SubqueryNode {
    pub inner_relation: String,
    pub correlation: Vec<CorrelationEq>,
    pub kind: SubqueryKind,
    pub output_column: String,
}

/// A planned query.
#[derive(Debug, Clone)]
pub struct QueryPlan {
    pub outer_relation: String,
    pub outer_select: Vec<String>,
    pub subquery: SubqueryNode,
}
