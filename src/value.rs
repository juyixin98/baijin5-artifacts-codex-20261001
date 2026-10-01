//! Typed domain values.
//!
//! The join domain is the union of SQL types that participate in natural-join
//! keys: 64-bit signed integers, UTF-8 strings and booleans. Each value carries
//! its type so cross-type comparisons are well defined instead of relying on
//! Rust enums' discriminant order.
//!
//! # NULL policy
//!
//! SQL semantics: **NULL never equals anything, including another NULL**
//! (`NULL = NULL` is UNKNOWN). A NULL in a natural-join key column therefore
//! cannot match across relations and the validator rejects such a request up
//! front with [`crate::ErrorCode::NullKey`] (the row is not silently dropped,
//! because silent dropping has produced real incidents). Non-key columns may
//! carry NULL; those flow through as nulls in the output batch.

use std::cmp::Ordering;

use serde::{Deserialize, Serialize};

/// A non-null key-domain value.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(tag = "type", content = "value", rename_all = "snake_case")]
pub enum Scalar {
    Int(i64),
    Str(String),
    Bool(bool),
}

impl Scalar {
    pub fn type_name(&self) -> &'static str {
        match self {
            Scalar::Int(_) => "int",
            Scalar::Str(_) => "string",
            Scalar::Bool(_) => "bool",
        }
    }

    /// Ordering within a type. Cross-type comparisons order by type name so
    /// the relation is total; the planner refuses mixed-type join keys anyway,
    /// this only keeps `Ord` honest.
    fn discriminate(&self, other: &Self) -> Ordering {
        match (self, other) {
            (Scalar::Int(a), Scalar::Int(b)) => a.cmp(b),
            (Scalar::Str(a), Scalar::Str(b)) => a.cmp(b),
            (Scalar::Bool(a), Scalar::Bool(b)) => a.cmp(b),
            (a, b) => a.type_name().cmp(b.type_name()),
        }
    }
}

impl PartialOrd for Scalar {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for Scalar {
    fn cmp(&self, other: &Self) -> Ordering {
        self.discriminate(other)
    }
}

/// A cell: either a present [`Scalar`] or NULL.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(untagged)]
pub enum Cell {
    Value(Scalar),
    Null,
}

impl PartialOrd for Cell {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for Cell {
    /// NULL sorts before any present value; otherwise delegate to the total
    /// order on [`Scalar`].
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        match (self, other) {
            (Cell::Null, Cell::Null) => std::cmp::Ordering::Equal,
            (Cell::Null, Cell::Value(_)) => std::cmp::Ordering::Less,
            (Cell::Value(_), Cell::Null) => std::cmp::Ordering::Greater,
            (Cell::Value(a), Cell::Value(b)) => a.cmp(b),
        }
    }
}

impl Cell {
    pub fn as_key(&self) -> Option<&Scalar> {
        match self {
            Cell::Value(v) => Some(v),
            Cell::Null => None,
        }
    }
}

impl From<i64> for Cell {
    fn from(v: i64) -> Self {
        Cell::Value(Scalar::Int(v))
    }
}

impl From<bool> for Cell {
    fn from(v: bool) -> Self {
        Cell::Value(Scalar::Bool(v))
    }
}

impl From<String> for Cell {
    fn from(v: String) -> Self {
        Cell::Value(Scalar::Str(v))
    }
}

impl From<&str> for Cell {
    fn from(v: &str) -> Self {
        Cell::Value(Scalar::Str(v.to_string()))
    }
}
