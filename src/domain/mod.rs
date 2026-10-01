//! Typed domain values, multiplicities, and the NULL policy.
//!
//! Every join key lives in [`Datum`]. Only types that participate in natural
//! joins need an ordering here; floats are intentionally unsupported as join
//! keys because `NaN`/`-0` make equality semantics surprising.
use std::cmp::Ordering;

use serde::{Deserialize, Serialize};

/// Multiplicity of a duplicate-free tuple inside a relation bag.
pub type Multiplicity = u64;

/// Logical type of a join-visible column.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LogicalType {
    Int64,
    Utf8,
    Boolean,
}

impl LogicalType {
    pub fn as_str(self) -> &'static str {
        match self {
            LogicalType::Int64 => "int64",
            LogicalType::Utf8 => "utf8",
            LogicalType::Boolean => "boolean",
        }
    }

    pub fn from_str_ci(s: &str) -> Option<Self> {
        match s.to_ascii_lowercase().as_str() {
            "int64" | "bigint" | "integer" => Some(LogicalType::Int64),
            "utf8" | "string" | "text" | "varchar" => Some(LogicalType::Utf8),
            "boolean" | "bool" => Some(LogicalType::Boolean),
            _ => None,
        }
    }
}

/// How NULL markers on join columns are treated.
///
/// SQL natural-join semantics are *never-equal*: NULL never matches NULL, so
/// such rows can never participate in a join. We make that the only default and
/// reject anything ambiguous at validation time rather than silently dropping.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum NullPolicy {
    /// Rows carrying NULL on any join variable are rejected up front (fail
    /// fast, no silent loss). This is the required, explicit default.
    #[default]
    Reject,
    /// NULL rows are excluded from join variables but kept in non-join
    /// projections; mirrors SQL `NATURAL JOIN` semantics. Counted in
    /// diagnostics so the drop is visible.
    DropJoinRows,
}

impl NullPolicy {
    /// Wire/JSON spelling matching the serde `snake_case` representation.
    pub fn as_str(self) -> &'static str {
        match self {
            NullPolicy::Reject => "reject",
            NullPolicy::DropJoinRows => "drop_join_rows",
        }
    }
}

/// A scalar domain element. Ordering is total *within a type*; cross-type
/// comparison only happens for type mismatches, which validation rejects.
///
/// `PartialEq`/`Eq` are structural and consistent with [`Ord`] (required by
/// sorting and binary search). SQL NULL semantics are *not* baked into
/// equality — use [`Datum::sql_eq`] at the NULL-policy boundary instead.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(untagged)]
pub enum Datum {
    Int(i64),
    Str(String),
    Bool(bool),
    /// Explicit NULL marker.
    #[default]
    Null,
}

impl Datum {
    pub fn logical_type(&self) -> Option<LogicalType> {
        match self {
            Datum::Int(_) => Some(LogicalType::Int64),
            Datum::Str(_) => Some(LogicalType::Utf8),
            Datum::Bool(_) => Some(LogicalType::Boolean),
            Datum::Null => None,
        }
    }

    /// SQL-style equality: NULL is never equal to anything, including NULL.
    pub fn sql_eq(&self, other: &Self) -> bool {
        match (self, other) {
            (Datum::Int(a), Datum::Int(b)) => a == b,
            (Datum::Bool(a), Datum::Bool(b)) => a == b,
            (Datum::Str(a), Datum::Str(b)) => a == b,
            _ => false,
        }
    }
}

/// Total order, grouped by type tag so a mismatched type is ordered apart (and
/// subsequently rejected by validation).
impl Ord for Datum {
    fn cmp(&self, other: &Self) -> Ordering {
        match (self, other) {
            (Datum::Int(a), Datum::Int(b)) => a.cmp(b),
            (Datum::Str(a), Datum::Str(b)) => a.cmp(b),
            (Datum::Bool(a), Datum::Bool(b)) => a.cmp(b),
            (Datum::Null, Datum::Null) => Ordering::Equal,
            // Deterministic cross-type order; validation rejects mixed joins.
            (Datum::Null, _) => Ordering::Less,
            (_, Datum::Null) => Ordering::Greater,
            (Datum::Int(_), _) => Ordering::Less,
            (_, Datum::Int(_)) => Ordering::Greater,
            (Datum::Bool(_), Datum::Str(_)) => Ordering::Less,
            (Datum::Str(_), Datum::Bool(_)) => Ordering::Greater,
        }
    }
}

impl PartialOrd for Datum {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn null_is_never_equal_even_to_null() {
        assert!(!Datum::Null.sql_eq(&Datum::Null));
        assert!(!Datum::Int(1).sql_eq(&Datum::Null));
        assert!(Datum::Int(1).sql_eq(&Datum::Int(1)));
        assert!(!Datum::Int(1).sql_eq(&Datum::Int(2)));
    }

    #[test]
    fn ordering_is_total_and_dedup_stable() {
        let mut v = vec![Datum::Int(3), Datum::Int(1), Datum::Int(2)];
        v.sort();
        assert_eq!(v, vec![Datum::Int(1), Datum::Int(2), Datum::Int(3)]);
    }
}
