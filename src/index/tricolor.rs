//! SQL three-valued logic (3VL) over the document universe.
//!
//! Every predicate evaluates to a [`Tricolor`]: three bitmaps `t`, `f`, `u`
//! over the *same* universe that partition it exactly:
//!
//! ```text
//! t ∪ f ∪ u = universe      t ∩ f = f ∩ u = u ∩ t = ∅
//! ```
//!
//! SQL truth tables (Kleene K3, where NULL means "unknown"):
//!
//! ```text
//! AND  T F U      OR   T F U      NOT  ->
//!   T  T F U        T  T T T          T  F
//!   F  F F F        F  T F U          F  T
//!   U  U F U        U  T U U          U  U
//! ```
//!
//! Two failure modes are deliberately avoided here:
//! * NOT is **not** `!t` per machine word. Unknown rows must stay unknown, and
//!   tail padding must stay zero. We derive all three sets inside the universe.
//! * Operators recompute the third set from the partition rather than trusting
//!   incoming bitmaps, and [`Tricolor::validate`] re-checks the partition.

use std::fmt;
use std::ops::Not;

use super::bitmap::Bitmap;
use crate::error::{Result, TviError};

/// A SQL ternary truth value per row.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Tri {
    True,
    False,
    Unknown,
}

impl Tri {
    /// SQL `AND`.
    #[must_use]
    pub fn and(self, o: Tri) -> Tri {
        match (self, o) {
            (Tri::False, _) | (_, Tri::False) => Tri::False,
            (Tri::True, Tri::True) => Tri::True,
            _ => Tri::Unknown,
        }
    }

    /// SQL `OR`.
    #[must_use]
    pub fn or(self, o: Tri) -> Tri {
        match (self, o) {
            (Tri::True, _) | (_, Tri::True) => Tri::True,
            (Tri::False, Tri::False) => Tri::False,
            _ => Tri::Unknown,
        }
    }
}

impl fmt::Display for Tri {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Tri::True => "TRUE",
            Tri::False => "FALSE",
            Tri::Unknown => "UNKNOWN",
        })
    }
}

/// SQL `NOT` on a scalar tri-value; UNKNOWN is a fixed point.
impl Not for Tri {
    type Output = Tri;
    fn not(self) -> Tri {
        match self {
            Tri::True => Tri::False,
            Tri::False => Tri::True,
            Tri::Unknown => Tri::Unknown,
        }
    }
}

/// The TRUE / FALSE / UNKNOWN row sets of one predicate, over one universe.
#[derive(Clone, Debug)]
pub struct Tricolor {
    pub t: Bitmap,
    pub f: Bitmap,
    pub u: Bitmap,
}

impl Tricolor {
    /// Assemble from the three sets and verify they partition the universe.
    pub fn new(t: Bitmap, f: Bitmap, u: Bitmap) -> Result<Self> {
        let tri = Tricolor { t, f, u };
        tri.validate()?;
        Ok(tri)
    }

    /// A predicate that is TRUE exactly on `t`; every other row is FALSE.
    /// Used for non-nullable predicates.
    pub fn from_true(t: Bitmap) -> Self {
        let n = t.len();
        let f = t.complement();
        let u = Bitmap::zeros(n);
        let tri = Tricolor { t, f, u };
        debug_assert!(tri.validate().is_ok());
        tri
    }

    /// TRUE on `t`, UNKNOWN on `u`, FALSE everywhere else in the universe.
    /// This is the constructor for nullable-column predicates: the caller
    /// supplies known-true rows and the column's NULL mask.
    pub fn from_true_and_null(t: Bitmap, null: Bitmap) -> Result<Self> {
        if t.len() != null.len() {
            return Err(TviError::UniverseMismatch {
                expected: t.len(),
                found: null.len(),
            });
        }
        // A NULL row cannot simultaneously be known-true; overlap would mean
        // the index and the null mask disagree (corrupt/mismatched versions).
        if t.and(&null)?.count_ones() != 0 {
            return Err(TviError::InvalidQuery(
                "true-set overlaps NULL mask: index is inconsistent with its column".into(),
            ));
        }
        let f = {
            let known = t.or(&null)?;
            known.complement()
        };
        let tri = Tricolor { t, f, u: null };
        tri.validate()?;
        Ok(tri)
    }

    /// Whole universe evaluates to one value (handy for constants/tests).
    pub fn constant(n: usize, v: Tri) -> Self {
        match v {
            Tri::True => Tricolor {
                t: all_ones(n),
                f: Bitmap::zeros(n),
                u: Bitmap::zeros(n),
            },
            Tri::False => Tricolor {
                t: Bitmap::zeros(n),
                f: all_ones(n),
                u: Bitmap::zeros(n),
            },
            Tri::Unknown => Tricolor {
                t: Bitmap::zeros(n),
                f: Bitmap::zeros(n),
                u: all_ones(n),
            },
        }
    }

    /// Universe size.
    pub fn len(&self) -> usize {
        self.t.len()
    }

    /// Whether the universe is empty.
    pub fn is_empty(&self) -> bool {
        self.t.is_empty()
    }

    /// Per-row value.
    pub fn value_at(&self, i: usize) -> Tri {
        if self.t.get(i) {
            Tri::True
        } else if self.f.get(i) {
            Tri::False
        } else {
            Tri::Unknown
        }
    }

    /// SQL `NOT`: FALSE = complement(TRUE) *within known rows*, and UNKNOWN
    /// is a fixed point. Never `!t_words`: that would move UNKNOWN rows into
    /// FALSE and set tail padding. Available as `!x` via [`Not`].
    #[must_use]
    pub fn negate(&self) -> Self {
        let tri = Tricolor {
            t: self.f.clone(),
            f: self.t.clone(),
            u: self.u.clone(),
        };
        debug_assert!(tri.validate().is_ok());
        tri
    }

    /// SQL `AND`. FALSE dominates, then UNKNOWN, then TRUE.
    pub fn and(&self, o: &Self) -> Result<Self> {
        self.check_universe(o)?;
        let t = self.t.and(&o.t)?;
        let f = self.f.or(&o.f)?;
        // Unknown = everything that is neither true nor false, derived inside
        // the universe instead of bitwise-combining the u sets.
        let known = t.or(&f)?;
        let u = known.complement();
        let tri = Tricolor { t, f, u };
        tri.validate()?;
        Ok(tri)
    }

    /// SQL `OR`. TRUE dominates, then UNKNOWN, then FALSE.
    pub fn or(&self, o: &Self) -> Result<Self> {
        self.check_universe(o)?;
        let t = self.t.or(&o.t)?;
        let f = self.f.and(&o.f)?;
        let known = t.or(&f)?;
        let u = known.complement();
        let tri = Tricolor { t, f, u };
        tri.validate()?;
        Ok(tri)
    }

    /// `x IS NULL` (SQL): TRUE where x is UNKNOWN, never UNKNOWN itself.
    #[must_use]
    pub fn is_null(&self) -> Self {
        // IS NULL has no UNKNOWN output: UNKNOWN -> TRUE, TRUE/FALSE -> FALSE.
        Tricolor::from_true(self.u.clone())
    }

    /// `x IS NOT NULL` (SQL): TRUE where x is not UNKNOWN.
    #[must_use]
    pub fn is_not_null(&self) -> Self {
        // IS NOT NULL has no UNKNOWN output: TRUE/FALSE -> TRUE, UNKNOWN -> FALSE.
        let known = self.t.or(&self.f).expect("t,f share universe");
        Tricolor::from_true(known)
    }

    /// Rows WHERE would accept (only TRUE; FALSE and UNKNOWN both rejected).
    pub fn selected(&self) -> &Bitmap {
        &self.t
    }

    /// Restrict the partition to a live-row subset.
    ///
    /// Version-deleted rows fall out of **all three** sets rather than being
    /// forced into FALSE, and the returned partition is verified to cover
    /// exactly the live set. The incoming live bitmap is over the same
    /// document universe, so combined-index compatibility is preserved.
    pub fn restrict_to_live(&self, live: &Bitmap) -> Result<Self> {
        self.t.check_same_universe(live)?;
        let tri = Tricolor {
            t: self.t.and(live)?,
            f: self.f.and(live)?,
            u: self.u.and(live)?,
        };
        tri.validate_cover(Some(live))?;
        Ok(tri)
    }

    /// The partition invariant: each row belongs to exactly one of t/f/u,
    /// tail padding included in none.
    pub fn validate(&self) -> Result<()> {
        self.validate_cover(None)
    }

    fn validate_cover(&self, cover: Option<&Bitmap>) -> Result<()> {
        let n = self.t.len();
        if self.f.len() != n || self.u.len() != n {
            return Err(TviError::UniverseMismatch {
                expected: n,
                found: self.f.len().max(self.u.len()),
            });
        }
        if let Some(m) = cover {
            if m.len() != n {
                return Err(TviError::UniverseMismatch {
                    expected: n,
                    found: m.len(),
                });
            }
        }
        if !self.t.is_disjoint(&self.f)
            || !self.f.is_disjoint(&self.u)
            || !self.u.is_disjoint(&self.t)
        {
            return Err(TviError::InvalidQuery(
                "tricolor sets overlap: a row is TRUE/FALSE/UNKNOWN more than once".into(),
            ));
        }
        let covered = self.t.or(&self.f)?.or(&self.u)?;
        let expected = cover.map(Bitmap::count_ones).unwrap_or(n);
        if covered.count_ones() != expected {
            return Err(TviError::InvalidQuery(format!(
                "tricolor does not cover the required universe: {expected} rows expected, {} classified ({} unclassified)",
                covered.count_ones(),
                expected - covered.count_ones()
            )));
        }
        // When covering a subset, every classified row must itself be live.
        if let Some(m) = cover {
            if !covered.is_subset(m) {
                return Err(TviError::InvalidQuery(
                    "tricolor classifies rows outside the live universe".into(),
                ));
            }
        }
        Ok(())
    }

    fn check_universe(&self, o: &Self) -> Result<()> {
        if self.len() != o.len() {
            return Err(TviError::UniverseMismatch {
                expected: self.len(),
                found: o.len(),
            });
        }
        Ok(())
    }

    /// One-character-per-row rendering for failure logs, e.g. `T F U T`.
    pub fn to_row_string(&self) -> String {
        (0..self.len())
            .map(|i| match self.value_at(i) {
                Tri::True => 'T',
                Tri::False => 'F',
                Tri::Unknown => 'U',
            })
            .collect()
    }
}

/// SQL `NOT` over a row partition; delegates to [`Tricolor::negate`].
impl Not for Tricolor {
    type Output = Tricolor;
    fn not(self) -> Tricolor {
        self.negate()
    }
}

impl Not for &Tricolor {
    type Output = Tricolor;
    fn not(self) -> Tricolor {
        self.negate()
    }
}

fn all_ones(n: usize) -> Bitmap {
    Bitmap::zeros(n).complement()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tri(n: usize, t: &[usize], f: &[usize], u: &[usize]) -> Tricolor {
        Tricolor::new(
            Bitmap::from_indices(n, t.iter().copied()).unwrap(),
            Bitmap::from_indices(n, f.iter().copied()).unwrap(),
            Bitmap::from_indices(n, u.iter().copied()).unwrap(),
        )
        .unwrap()
    }

    #[test]
    fn null_and_truth_table_row_by_row() {
        // Classic SQL: NULL AND FALSE = FALSE, NULL AND TRUE = UNKNOWN.
        let n = 2;
        let a = tri(n, &[0], &[], &[1]); // T, U
        let b = tri(n, &[], &[0, 1], &[]); // F, F
        let r = a.and(&b).unwrap();
        assert_eq!(r.value_at(0), Tri::False);
        assert_eq!(r.value_at(1), Tri::False); // U AND F = F, not U
    }

    #[test]
    fn null_or_truth_table_row_by_row() {
        let n = 2;
        let a = tri(n, &[], &[0], &[1]); // F, U
        let b = tri(n, &[0, 1], &[], &[]); // T, T
        let r = a.or(&b).unwrap();
        assert_eq!(r.value_at(0), Tri::True);
        assert_eq!(r.value_at(1), Tri::True); // U OR T = T
    }

    #[test]
    fn not_is_fixpoint_on_unknown_and_clears_tail() {
        // 65 rows, the last row UNKNOWN, rest TRUE.
        let n = 65;
        let a = tri(n, &(0..64).collect::<Vec<_>>(), &[], &[64]);
        let r = !a;
        assert_eq!(r.value_at(0), Tri::False);
        assert_eq!(r.value_at(64), Tri::Unknown);
        assert_eq!(r.t.count_ones() + r.f.count_ones() + r.u.count_ones(), n);
    }

    #[test]
    fn is_null_collapses_unknown_to_true() {
        let n = 3;
        let a = tri(n, &[0], &[1], &[2]);
        let isn = a.is_null();
        assert_eq!(isn.value_at(0), Tri::False);
        assert_eq!(isn.value_at(1), Tri::False);
        assert_eq!(isn.value_at(2), Tri::True);
        assert_eq!(isn.u.count_ones(), 0);
    }

    #[test]
    fn rejects_overlap_and_uncovered_rows() {
        let n = 2;
        let bad = Tricolor::new(
            Bitmap::from_indices(n, [0]).unwrap(),
            Bitmap::from_indices(n, [0]).unwrap(),
            Bitmap::zeros(n),
        );
        assert!(matches!(bad, Err(TviError::InvalidQuery(_))));
    }
}
