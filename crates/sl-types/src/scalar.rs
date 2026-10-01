//! Owned scalar values.
//!
//! The streaming operator cannot keep every input arrow batch alive (that
//! would make retained state O(total input) and render memory budgets
//! meaningless). Instead it extracts the columns it needs into owned
//! [`Scalar`]s: the sort-key columns live on every heap entry, and the full
//! row is stored so the final output can be rebuilt without the source.

use arrow2::array::{Array, BooleanArray, Float64Array, Int64Array, Utf8Array};

use crate::error::{Result, SlError};
use crate::schema::ColumnType;

/// One owned nullable value.
///
/// Float comparison uses [`f64::total_cmp`] (IEEE-754 total order): NaNs get a
/// deterministic place, and two NaNs are treated as tied only when their bit
/// patterns compare equal. This boundary behaviour is documented in
/// `docs/SEMANTICS.md`.
#[derive(Debug, Clone)]
pub enum Scalar {
    Null,
    I64(i64),
    F64(f64),
    Utf8(String),
    Boolean(bool),
}

impl Scalar {
    pub fn is_null(&self) -> bool {
        matches!(self, Scalar::Null)
    }

    pub fn ty(&self) -> Option<ColumnType> {
        match self {
            Scalar::Null => None,
            Scalar::I64(_) => Some(ColumnType::Int64),
            Scalar::F64(_) => Some(ColumnType::Float64),
            Scalar::Utf8(_) => Some(ColumnType::Utf8),
            Scalar::Boolean(_) => Some(ColumnType::Boolean),
        }
    }

    /// Heap footprint estimate in bytes. Used for budget accounting and peak
    /// state stats; intentionally an approximation with a documented basis
    /// (enum discriminant + payload + backing bytes for strings).
    pub fn estimated_bytes(&self) -> usize {
        // size_of discriminant + largest variant (String = 24 bytes payload).
        std::mem::size_of::<Scalar>()
            + match self {
                Scalar::Utf8(s) => s.capacity(),
                _ => 0,
            }
    }
}

/// Ordering that honours NULL placement. `nulls_first` decides where a NULL
/// goes relative to a non-NULL *independently of ascending/descending*
/// (matching arrow2's convention); the caller reverses the value order for
/// DESC. Two NULLs compare equal so the next key (then identity) decides.
pub fn null_aware_cmp(
    a: &Scalar,
    b: &Scalar,
    nulls_first: bool,
) -> std::cmp::Ordering {
    use std::cmp::Ordering;
    match (a, b) {
        (Scalar::Null, Scalar::Null) => Ordering::Equal,
        (Scalar::Null, _) => {
            if nulls_first {
                Ordering::Less
            } else {
                Ordering::Greater
            }
        }
        (_, Scalar::Null) => {
            if nulls_first {
                Ordering::Greater
            } else {
                Ordering::Less
            }
        }
        _ => value_cmp(a, b),
    }
}

/// Compare two guaranteed non-null values. Mismatched variants are an
/// internal invariant violation (columns are typed and validated).
pub fn value_cmp(a: &Scalar, b: &Scalar) -> std::cmp::Ordering {
    use std::cmp::Ordering;
    match (a, b) {
        (Scalar::I64(x), Scalar::I64(y)) => x.cmp(y),
        (Scalar::F64(x), Scalar::F64(y)) => x.total_cmp(y),
        (Scalar::Utf8(x), Scalar::Utf8(y)) => x.cmp(y),
        (Scalar::Boolean(x), Scalar::Boolean(y)) => x.cmp(y),
        _ => Ordering::Equal, // unreachable for well-typed columns
    }
}

/// Equality of *user sort keys* used for WITH TIES. Two NULLs tie; floats tie
/// under total-order equality (identical bit patterns, NaN included).
pub fn key_equal(a: &Scalar, b: &Scalar) -> bool {
    null_aware_cmp(a, b, true) == std::cmp::Ordering::Equal
}

/// Extract one typed column into owned scalars.
pub fn extract_column(array: &dyn Array, ty: ColumnType) -> Result<Vec<Scalar>> {
    let n = array.len();
    let mut out = Vec::with_capacity(n);
    match ty {
        ColumnType::Int64 => {
            let a = array
                .as_any()
                .downcast_ref::<Int64Array>()
                .ok_or_else(downcast_err("Int64Array"))?;
            for i in 0..n {
                out.push(a.get(i).map_or(Scalar::Null, Scalar::I64));
            }
        }
        ColumnType::Float64 => {
            let a = array
                .as_any()
                .downcast_ref::<Float64Array>()
                .ok_or_else(downcast_err("Float64Array"))?;
            for i in 0..n {
                out.push(a.get(i).map_or(Scalar::Null, Scalar::F64));
            }
        }
        ColumnType::Utf8 => {
            let a = array
                .as_any()
                .downcast_ref::<Utf8Array<i32>>()
                .ok_or_else(downcast_err("Utf8Array<i32>"))?;
            for i in 0..n {
                out.push(a.get(i).map_or(Scalar::Null, |s| Scalar::Utf8(s.to_owned())));
            }
        }
        ColumnType::Boolean => {
            let a = array
                .as_any()
                .downcast_ref::<BooleanArray>()
                .ok_or_else(downcast_err("BooleanArray"))?;
            for i in 0..n {
                out.push(a.get(i).map_or(Scalar::Null, Scalar::Boolean));
            }
        }
    }
    Ok(out)
}

fn downcast_err(expected: &'static str) -> impl FnOnce() -> SlError {
    move || {
        SlError::internal(format!("arrow array was not {expected} despite schema validation"))
    }
}
