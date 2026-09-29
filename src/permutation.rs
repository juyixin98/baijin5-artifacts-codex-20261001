//! Sorting permutations and position mappings.
//!
//! IEJoin needs, for each side of each predicate, a *stable* permutation of row
//! indices sorted by the predicate key, plus the inverse map
//! `row index -> sorted position`. Strict (`<`) and non-strict (`<=`) joins
//! differ in how equal-key groups are treated at boundaries:
//!
//! * Non-strict: equal keys belong to the match interval; on the greater side
//!   the interval starts at the *beginning* of the equal run, on the smaller
//!   side it ends at the *end* of the equal run.
//! * Strict: equal keys are excluded; the interval starts/ends at the opposite
//!   edge of the equal run.
//!
//! NULLs sort into a contiguous leading prefix but are never part of a match
//! interval. Duplicate-valued rows keep distinct identities: stability of the
//! permutation guarantees rows with the same key occupy adjacent, distinct
//! slots and both participate independently.

use std::cmp::Ordering;

use crate::types::Scalar;

/// Direction of a sort. Ascending keys pair with the greater-value side of a
/// predicate (`l`, predicate `r < l`), descending keys with the smaller-value
/// side (`r`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SortOrder {
    Asc,
    Desc,
}

/// A sorted view over one key column.
#[derive(Debug, Clone)]
pub struct SortedColumn {
    /// `perm[pos] = original row index`, in scan order.
    perm: Vec<usize>,
    /// Key values in scan order: `sorted[pos] = values[perm[pos]]`.
    sorted: Vec<Scalar>,
    /// `pos_of[row] = position` in [`Self::perm`].
    pos_of: Vec<usize>,
    /// Number of leading positions that are NULL.
    null_end: usize,
    order: SortOrder,
}

impl SortedColumn {
    /// Stable sort `values` in the requested order. NULLs form a leading
    /// prefix in both orders; non-null values follow ascending or descending.
    pub fn build(values: &[Scalar], order: SortOrder) -> Self {
        let n = values.len();
        let mut perm: Vec<usize> = (0..n).collect();
        // sort_by is stable → equal keys keep their original (identity) order.
        perm.sort_by(|&a, &b| match (values[a].is_null(), values[b].is_null()) {
            (true, true) => Ordering::Equal,
            (true, false) => Ordering::Less,
            (false, true) => Ordering::Greater,
            (false, false) => match order {
                SortOrder::Asc => values[a].sort_cmp(&values[b]),
                SortOrder::Desc => values[b].sort_cmp(&values[a]),
            },
        });
        let sorted: Vec<Scalar> = perm.iter().map(|&r| values[r].clone()).collect();
        let mut pos_of = vec![0usize; n];
        let mut null_end = 0usize;
        for (pos, &row) in perm.iter().enumerate() {
            pos_of[row] = pos;
            if values[row].is_null() {
                null_end = pos + 1;
            }
        }
        SortedColumn {
            perm,
            sorted,
            pos_of,
            null_end,
            order,
        }
    }

    pub fn len(&self) -> usize {
        self.perm.len()
    }
    pub fn is_empty(&self) -> bool {
        self.perm.is_empty()
    }
    pub fn order(&self) -> SortOrder {
        self.order
    }
    pub fn permutation(&self) -> &[usize] {
        &self.perm
    }
    pub fn sorted_values(&self) -> &[Scalar] {
        &self.sorted
    }
    pub fn position_of(&self, row: usize) -> usize {
        self.pos_of[row]
    }
    pub fn null_end(&self) -> usize {
        self.null_end
    }

    /// Ascending order only: exclusive end `hi` of the positions whose value
    /// is `< key` (strict) or `<= key` (non-strict). Matching positions are
    /// `[null_end, hi)`; NULL positions are never included.
    pub fn asc_prefix_len(&self, key: &Scalar, strict: bool) -> usize {
        debug_assert_eq!(self.order, SortOrder::Asc);
        if key.is_null() {
            return self.null_end;
        }
        let nn = &self.sorted[self.null_end..];
        let p = if strict {
            partition_point(nn, |v| v.sort_cmp(key) == Ordering::Less)
        } else {
            partition_point(nn, |v| v.sort_cmp(key) != Ordering::Greater)
        };
        self.null_end + p
    }

    /// Ascending order only: first position whose value is `> key` (strict,
    /// used when this side is the greater operand of `<`) or `>= key`
    /// (non-strict).
    pub fn asc_suffix_start(&self, key: &Scalar, strict: bool) -> usize {
        debug_assert_eq!(self.order, SortOrder::Asc);
        if key.is_null() {
            return self.sorted.len();
        }
        let nn = &self.sorted[self.null_end..];
        let p = if strict {
            partition_point(nn, |v| v.sort_cmp(key) != Ordering::Greater)
        } else {
            partition_point(nn, |v| v.sort_cmp(key) == Ordering::Less)
        };
        self.null_end + p
    }

    /// Descending order only: exclusive end of the leading positions whose
    /// value is `> key` (strict) or `>= key` (non-strict). The matching prefix
    /// is `[null_end, desc_prefix_len)`; NULL positions are excluded.
    pub fn desc_prefix_len(&self, key: &Scalar, strict: bool) -> usize {
        debug_assert_eq!(self.order, SortOrder::Desc);
        if key.is_null() {
            return self.null_end;
        }
        let nn = &self.sorted[self.null_end..];
        let p = if strict {
            partition_point(nn, |v| v.sort_cmp(key) == Ordering::Greater)
        } else {
            partition_point(nn, |v| v.sort_cmp(key) != Ordering::Less)
        };
        self.null_end + p
    }

    /// Half-open match interval `[lo, hi)` of positions whose values satisfy
    /// the inequality against `key`.
    ///
    /// The role of this column is fixed by its [`SortOrder`]:
    /// * [`SortOrder::Asc`] is the greater-value side: match is
    ///   `value >= key` (non-strict) or `value > key` (strict) — a suffix.
    /// * [`SortOrder::Desc`] is the smaller-value side: match is
    ///   `value <= key` (non-strict) or `value < key` (strict). Non-strict
    ///   match is the equal run plus the strictly-smaller suffix, i.e.
    ///   `[first <= key, first < key)`; strict match is `[first < key, end)`.
    pub fn match_interval(&self, key: &Scalar, strict: bool) -> Option<(usize, usize)> {
        if key.is_null() || self.null_end == self.sorted.len() {
            return None;
        }
        let end = self.sorted.len();
        let nn = &self.sorted[self.null_end..];
        match self.order {
            SortOrder::Asc => {
                let p = if strict {
                    partition_point(nn, |v| v.sort_cmp(key) != Ordering::Greater)
                } else {
                    partition_point(nn, |v| v.sort_cmp(key) == Ordering::Less)
                };
                let lo = self.null_end + p;
                (lo < end).then_some((lo, end))
            }
            SortOrder::Desc => {
                // nn is descending: values > key, then the equal run, then
                // strictly smaller values.
                let first_le =
                    self.null_end + partition_point(nn, |v| v.sort_cmp(key) == Ordering::Greater);
                if strict {
                    let first_lt =
                        self.null_end + partition_point(nn, |v| v.sort_cmp(key) != Ordering::Less);
                    (first_lt < end).then_some((first_lt, end))
                } else {
                    (first_le < end).then_some((first_le, end))
                }
            }
        }
    }
}

/// Index of the first element for which `pred` is false, assuming `pred`
/// returns `true` on a prefix and `false` afterwards (the sorted partition).
fn partition_point(sorted: &[Scalar], pred: impl Fn(&Scalar) -> bool) -> usize {
    let mut lo = 0usize;
    let mut hi = sorted.len();
    while lo < hi {
        let mid = lo + (hi - lo) / 2;
        if pred(&sorted[mid]) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    lo
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::builder::int_column;

    fn ints(vals: Vec<Option<i64>>) -> Vec<Scalar> {
        int_column("k", vals).to_scalars()
    }

    #[test]
    fn stable_permutation_preserves_duplicate_identity() {
        let v = ints(vec![Some(5), Some(1), Some(5), Some(1)]);
        let asc = SortedColumn::build(&v, SortOrder::Asc);
        // Equal keys keep original relative order.
        assert_eq!(asc.permutation(), &[1, 3, 0, 2]);
        let mut back = vec![0usize; 4];
        for (pos, &row) in asc.permutation().iter().enumerate() {
            back[row] = pos;
        }
        assert_eq!(back, asc.pos_of); // dummy field is private; checked via position_of
        assert_eq!(asc.position_of(0), 2);
        assert_eq!(asc.position_of(2), 3);
    }

    #[test]
    fn nulls_form_leading_prefix_and_never_match() {
        let v = ints(vec![None, Some(3), None, Some(1)]);
        let asc = SortedColumn::build(&v, SortOrder::Asc);
        assert_eq!(asc.null_end(), 2);
        assert_eq!(asc.permutation()[..2], [0, 2]);
        assert!(asc.match_interval(&Scalar::Null, false).is_none());
        let desc = SortedColumn::build(&v, SortOrder::Desc);
        assert_eq!(desc.null_end(), 2);
        assert!(desc.match_interval(&Scalar::Null, true).is_none());
    }

    #[test]
    fn strict_vs_nonstrict_equal_group_boundaries_asc() {
        let v = ints(vec![Some(1), Some(2), Some(2), Some(2), Some(3)]);
        let asc = SortedColumn::build(&v, SortOrder::Asc);
        // greater side, key = 2
        assert_eq!(asc.match_interval(&Scalar::Int(2), false), Some((1, 5)));
        assert_eq!(asc.match_interval(&Scalar::Int(2), true), Some((4, 5)));
        // key beyond the domain → no match
        assert_eq!(asc.match_interval(&Scalar::Int(3), true), None);
        assert_eq!(asc.match_interval(&Scalar::Int(0), false), Some((0, 5)));
    }

    #[test]
    fn strict_vs_nonstrict_equal_group_boundaries_desc() {
        let v = ints(vec![Some(1), Some(2), Some(2), Some(2), Some(3)]);
        let desc = SortedColumn::build(&v, SortOrder::Desc);
        // permuted order: 3,2,2,2,1 ; smaller side, key = 2
        assert_eq!(desc.match_interval(&Scalar::Int(2), false), Some((1, 5)));
        assert_eq!(desc.match_interval(&Scalar::Int(2), true), Some((4, 5)));
        assert_eq!(desc.match_interval(&Scalar::Int(1), false), Some((4, 5)));
        assert_eq!(desc.match_interval(&Scalar::Int(1), true), None);
    }

    #[test]
    fn all_equal_values_strict_is_empty_nonstrict_is_all() {
        let v = ints(vec![Some(7); 4]);
        let asc = SortedColumn::build(&v, SortOrder::Asc);
        let desc = SortedColumn::build(&v, SortOrder::Desc);
        assert_eq!(asc.match_interval(&Scalar::Int(7), false), Some((0, 4)));
        assert_eq!(asc.match_interval(&Scalar::Int(7), true), None);
        assert_eq!(desc.match_interval(&Scalar::Int(7), false), Some((0, 4)));
        assert_eq!(desc.match_interval(&Scalar::Int(7), true), None);
    }

    #[test]
    fn empty_column_intervals_are_none() {
        let v: Vec<Scalar> = vec![];
        let asc = SortedColumn::build(&v, SortOrder::Asc);
        assert_eq!(asc.len(), 0);
        assert_eq!(asc.match_interval(&Scalar::Int(1), false), None);
    }
}
