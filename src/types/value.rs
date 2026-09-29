//! Typed scalar values with total ordering for sorting.
//!
//! NULL semantics:
//! * For *ordering* (permutation construction) NULL sorts before every
//!   non-null value, deterministically. This gives equal-key duplicates a
//!   stable relative order.
//! * For *predicate evaluation* NULL never matches: any comparison whose left
//!   or right operand is NULL is false (SQL three-valued boolean → not emitted).

use std::cmp::Ordering;

/// Supported key physical types. Kept small on purpose: the range-join problem
/// needs a dense order, nothing else.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum KeyType {
    Int64,
    Float64,
    Utf8,
}

impl KeyType {
    pub fn as_str(self) -> &'static str {
        match self {
            KeyType::Int64 => "int64",
            KeyType::Float64 => "float64",
            KeyType::Utf8 => "utf8",
        }
    }

    pub fn parse(s: &str) -> Option<KeyType> {
        match s {
            "int64" => Some(KeyType::Int64),
            "float64" => Some(KeyType::Float64),
            "utf8" => Some(KeyType::Utf8),
            _ => None,
        }
    }
}

/// A scalar key. Float keys are canonicalized: NaN → NULL, `-0.0 == 0.0`.
#[derive(Debug, Clone)]
pub enum Scalar {
    Null,
    Int(i64),
    /// Always finite; NaN is represented as [`Scalar::Null`].
    Float(ordered_float::OrderedFloat),
    Text(std::sync::Arc<str>),
}

/// Minimal newtype so floats are `Eq`/`Ord` without pulling another crate.
pub mod ordered_float {
    use std::cmp::Ordering;

    /// Finite f64 with total order (NaN never stored inside).
    #[derive(Debug, Clone, Copy)]
    pub struct OrderedFloat(pub f64);

    impl PartialEq for OrderedFloat {
        fn eq(&self, other: &Self) -> bool {
            self.0.total_cmp(&other.0) == Ordering::Equal
        }
    }
    impl Eq for OrderedFloat {}
    impl PartialOrd for OrderedFloat {
        fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
            Some(self.cmp(other))
        }
    }
    impl Ord for OrderedFloat {
        fn cmp(&self, other: &Self) -> Ordering {
            self.0.total_cmp(&other.0)
        }
    }
}

impl Scalar {
    pub fn from_f64(v: f64) -> Self {
        if v.is_nan() {
            Scalar::Null
        } else if v == 0.0 {
            // Canonicalize -0.0 to +0.0 so the two compare equal as keys.
            Scalar::Float(ordered_float::OrderedFloat(0.0))
        } else {
            Scalar::Float(ordered_float::OrderedFloat(v))
        }
    }

    pub fn from_str_value(v: &str) -> Self {
        Scalar::Text(std::sync::Arc::from(v))
    }

    pub fn is_null(&self) -> bool {
        matches!(self, Scalar::Null)
    }

    pub fn key_type(&self) -> Option<KeyType> {
        match self {
            Scalar::Null => None,
            Scalar::Int(_) => Some(KeyType::Int64),
            Scalar::Float(_) => Some(KeyType::Float64),
            Scalar::Text(_) => Some(KeyType::Utf8),
        }
    }

    /// Ordering used by permutation construction. NULL sorts first.
    /// Types are assumed homogeneous (checked when the batch is built);
    /// cross-type comparisons here are deterministic but semantically invalid
    /// plans never reach this point.
    pub fn sort_cmp(&self, other: &Self) -> Ordering {
        match (self, other) {
            (Scalar::Null, Scalar::Null) => Ordering::Equal,
            (Scalar::Null, _) => Ordering::Less,
            (_, Scalar::Null) => Ordering::Greater,
            (Scalar::Int(a), Scalar::Int(b)) => a.cmp(b),
            (Scalar::Float(a), Scalar::Float(b)) => a.cmp(b),
            (Scalar::Text(a), Scalar::Text(b)) => a.cmp(b),
            // Homogeneity is validated upstream; keep a deterministic fallback.
            (Scalar::Int(_), Scalar::Float(_)) => Ordering::Less,
            (Scalar::Float(_), Scalar::Int(_)) => Ordering::Greater,
            (Scalar::Text(_), _) => Ordering::Greater,
            (_, Scalar::Text(_)) => Ordering::Less,
        }
    }

    /// SQL-style strict comparison: NULL on either side yields `false`.
    pub fn lt(&self, other: &Self) -> bool {
        !self.is_null() && !other.is_null() && self.sort_cmp(other) == Ordering::Less
    }
    pub fn le(&self, other: &Self) -> bool {
        !self.is_null()
            && !other.is_null()
            && matches!(self.sort_cmp(other), Ordering::Less | Ordering::Equal)
    }
    pub fn gt(&self, other: &Self) -> bool {
        other.lt(self)
    }
    pub fn ge(&self, other: &Self) -> bool {
        other.le(self)
    }
}

/// Total order used for permutation construction. NOTE: equality here is
/// *sort* equality — predicates must still go through [`Scalar::le`] etc. so
/// NULL mismatch is honoured.
impl PartialEq for Scalar {
    fn eq(&self, other: &Self) -> bool {
        self.sort_cmp(other) == Ordering::Equal
    }
}
impl Eq for Scalar {}
impl PartialOrd for Scalar {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
impl Ord for Scalar {
    fn cmp(&self, other: &Self) -> Ordering {
        self.sort_cmp(other)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn null_never_matches_predicates() {
        assert!(!Scalar::Null.lt(&Scalar::Int(1)));
        assert!(!Scalar::Int(1).lt(&Scalar::Null));
        assert!(!Scalar::Null.le(&Scalar::Null));
        assert!(!Scalar::Null.ge(&Scalar::Null));
        assert!(!Scalar::Null.gt(&Scalar::Null));
    }

    #[test]
    fn float_nan_becomes_null() {
        assert!(Scalar::from_f64(f64::NAN).is_null());
        assert_eq!(Scalar::from_f64(-0.0), Scalar::from_f64(0.0));
    }
}
