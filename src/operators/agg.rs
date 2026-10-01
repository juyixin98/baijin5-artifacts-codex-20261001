//! Scalar aggregates for the restricted fragment.
//!
//! Semantics (SQL):
//!
//! * `COUNT(col)` counts **non-NULL** values; an empty group yields `0`.
//! * `SUM(col)` sums non-NULL values; a group with no non-NULL value
//!   (including an empty group) yields NULL.
//!
//! This COUNT/SUM distinction on empty groups is one of the properties the
//! equivalence tests pin down.

use crate::batch::Scalar;
use crate::error::{ErrorKind, QError, QResult};
use crate::query::AggKind;

/// Accumulator for one group of one aggregate.
#[derive(Debug, Clone)]
pub enum AggAcc {
    Count { count: i64 },
    Sum { sum: i64, seen: bool },
}

impl AggAcc {
    pub fn new(func: AggKind) -> Self {
        match func {
            AggKind::Count => AggAcc::Count { count: 0 },
            AggKind::Sum => AggAcc::Sum {
                sum: 0,
                seen: false,
            },
        }
    }

    pub fn update(&mut self, v: &Scalar) -> QResult<()> {
        match (self, v) {
            (AggAcc::Count { count }, Scalar::Int(_)) => {
                *count = count.checked_add(1).ok_or_else(|| {
                    QError::new(ErrorKind::NumericOverflow, "COUNT overflowed int64")
                })?;
            }
            // COUNT(*) style NULL rows are ignored for COUNT(col).
            (AggAcc::Count { .. }, Scalar::Null) | (AggAcc::Count { .. }, Scalar::Str(_)) => {}
            (AggAcc::Sum { sum, seen }, Scalar::Int(i)) => {
                *sum = sum.checked_add(*i).ok_or_else(|| {
                    QError::new(ErrorKind::NumericOverflow, "SUM overflowed int64")
                })?;
                *seen = true;
            }
            (AggAcc::Sum { .. }, Scalar::Null) | (AggAcc::Sum { .. }, Scalar::Str(_)) => {}
        }
        Ok(())
    }

    pub fn finish(self) -> Scalar {
        match self {
            AggAcc::Count { count } => Scalar::Int(count),
            AggAcc::Sum { sum, seen } => {
                if seen {
                    Scalar::Int(sum)
                } else {
                    Scalar::Null
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    #[test]
    fn count_empty_group_is_zero_sum_empty_group_is_null() {
        let mut c = AggAcc::new(AggKind::Count);
        c.update(&Scalar::Null).unwrap();
        assert_eq!(c.finish(), Scalar::Int(0));

        let mut s = AggAcc::new(AggKind::Sum);
        s.update(&Scalar::Null).unwrap();
        assert_eq!(s.finish(), Scalar::Null);
    }

    #[test]
    fn sum_ignores_nulls_but_remembers_nonnull() {
        let mut s = AggAcc::new(AggKind::Sum);
        s.update(&Scalar::Int(3)).unwrap();
        s.update(&Scalar::Null).unwrap();
        s.update(&Scalar::Int(4)).unwrap();
        assert_eq!(s.finish(), Scalar::Int(7));
    }

    #[test]
    fn count_ignores_nulls() {
        let mut c = AggAcc::new(AggKind::Count);
        for v in [Scalar::Int(1), Scalar::Null, Scalar::Int(2)] {
            c.update(&v).unwrap();
        }
        assert_eq!(c.finish(), Scalar::Int(2));
    }

    #[test]
    fn sum_overflow_is_reported() {
        let mut s = AggAcc::new(AggKind::Sum);
        s.update(&Scalar::Int(i64::MAX)).unwrap();
        let err = s.update(&Scalar::Int(1)).unwrap_err();
        assert_eq!(err.kind, ErrorKind::NumericOverflow);
    }

    #[test]
    fn str_in_int_aggregate_is_ignored_not_panicked() {
        let mut c = AggAcc::new(AggKind::Count);
        c.update(&Scalar::Str(Arc::from("x"))).unwrap();
        assert_eq!(c.finish(), Scalar::Int(0));
    }
}
