//! SQL three-valued logic (3VL): every document in one shared universe belongs
//! to EXACTLY one of three sets — TRUE, FALSE, UNKNOWN.
//!
//! The three sets always operate inside the same *alive* universe (deleted rows
//! are outside it). NOT swaps the TRUE/FALSE sets while leaving UNKNOWN in place;
//! it is computed by set difference against the alive universe, NEVER by a
//! machine-word `!true` (which would pull padding bits and deleted rows into the
//! result).

use crate::bits::Bitmap;
use crate::error::{Error, ErrorKind, Result};

/// Scalar SQL truth value. The independent test oracle uses the same enum.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tri3 {
    True,
    False,
    /// SQL UNKNOWN: produced by NULL comparisons / NULL operands.
    Unknown,
}

impl Tri3 {
    /// Kleene NOT (named `negate` to avoid confusion with [`std::ops::Not`]).
    pub fn negate(self) -> Self {
        match self {
            Tri3::True => Tri3::False,
            Tri3::False => Tri3::True,
            Tri3::Unknown => Tri3::Unknown,
        }
    }

    /// Kleene AND.
    pub fn and(self, other: Self) -> Self {
        match (self, other) {
            (Tri3::True, Tri3::True) => Tri3::True,
            (Tri3::False, _) | (_, Tri3::False) => Tri3::False,
            _ => Tri3::Unknown,
        }
    }

    /// Kleene OR.
    pub fn or(self, other: Self) -> Self {
        match (self, other) {
            (Tri3::False, Tri3::False) => Tri3::False,
            (Tri3::True, _) | (_, Tri3::True) => Tri3::True,
            _ => Tri3::Unknown,
        }
    }
}

/// Partition of the alive universe into TRUE / FALSE / UNKNOWN row sets.
///
/// Invariant (checked by [`TriSet::validate`]):
///
/// * `t` and `f` are disjoint;
/// * both are subsets of `alive`;
/// * UNKNOWN = alive − (t ∪ f).
///
/// Therefore every alive row is in exactly one set, and deleted/padding rows
/// are in none.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TriSet {
    alive: Bitmap,
    t: Bitmap,
    f: Bitmap,
}

impl TriSet {
    /// Build from explicit TRUE/FALSE sets and an alive universe.
    /// UNKNOWN is the remainder. Validates the partition invariant.
    pub fn from_tf(alive: Bitmap, t: Bitmap, f: Bitmap) -> Result<Self> {
        let ts = TriSet { alive, t, f };
        ts.validate()?;
        Ok(ts)
    }

    /// Build from one scalar state per row position.
    /// Rows outside `alive` must be given as [`Tri3::Unknown`] (they are ignored
    /// — UNKNOWN only ever covers alive rows); a TRUE/FALSE value on a deleted
    /// row is rejected.
    pub fn from_states(alive: &Bitmap, states: &[Tri3]) -> Result<Self> {
        if states.len() != alive.len() {
            return Err(Error::new(
                ErrorKind::UniverseMismatch,
                format!("states len {} != universe {}", states.len(), alive.len()),
            ));
        }
        let mut t = Bitmap::zeros(alive.len());
        let mut f = Bitmap::zeros(alive.len());
        for (i, s) in states.iter().enumerate() {
            match s {
                Tri3::True => {
                    if !alive.get(i) {
                        return Err(Error::new(
                            ErrorKind::DeletesetMismatch,
                            format!("row {i} marked TRUE but is not alive"),
                        ));
                    }
                    t.set(i, true);
                }
                Tri3::False => {
                    if !alive.get(i) {
                        return Err(Error::new(
                            ErrorKind::DeletesetMismatch,
                            format!("row {i} marked FALSE but is not alive"),
                        ));
                    }
                    f.set(i, true);
                }
                Tri3::Unknown => {}
            }
        }
        Self::from_tf(alive.clone(), t, f)
    }

    /// All-alive UNKNOWN tri-set (used for NULL-only predicates over a column
    /// whose value is absent).
    pub fn all_unknown(alive: Bitmap) -> Self {
        let n = alive.len();
        Self {
            alive,
            t: Bitmap::zeros(n),
            f: Bitmap::zeros(n),
        }
    }

    /// `IS NULL` over a column null-mask: NULL rows -> TRUE, other alive rows
    /// -> FALSE. The result never contains UNKNOWN.
    pub fn is_null(alive: &Bitmap, nulls: &Bitmap) -> Result<Self> {
        if nulls.len() != alive.len() {
            return Err(Error::new(
                ErrorKind::UniverseMismatch,
                format!("null mask len {} != universe {}", nulls.len(), alive.len()),
            ));
        }
        // A deleted row must never surface as NULL: intersect with alive.
        let t = nulls.and(alive)?;
        // FALSE = alive rows that are not null.
        let f = alive.and_not(nulls)?;
        Self::from_tf(alive.clone(), t, f)
    }

    pub fn len(&self) -> usize {
        self.alive.len()
    }

    pub fn is_empty(&self) -> bool {
        self.alive.len() == 0
    }

    pub fn alive(&self) -> &Bitmap {
        &self.alive
    }
    pub fn true_set(&self) -> &Bitmap {
        &self.t
    }
    pub fn false_set(&self) -> &Bitmap {
        &self.f
    }

    /// UNKNOWN = alive − TRUE − FALSE (computed, never stored as machine NOT).
    pub fn unknown_set(&self) -> Bitmap {
        let known = self.t.or(&self.f).expect("t/f share universe");
        self.alive
            .and_not(&known)
            .expect("alive shares universe with known")
    }

    pub fn state_at(&self, row: usize) -> Option<Tri3> {
        if row >= self.alive.len() || !self.alive.get(row) {
            return None;
        }
        if self.t.get(row) {
            Some(Tri3::True)
        } else if self.f.get(row) {
            Some(Tri3::False)
        } else {
            Some(Tri3::Unknown)
        }
    }

    /// One scalar state per row for alive rows; `None` for deleted rows.
    pub fn row_states(&self) -> Vec<Option<Tri3>> {
        (0..self.alive.len()).map(|i| self.state_at(i)).collect()
    }

    pub fn count_true(&self) -> usize {
        self.t.count_ones()
    }
    pub fn count_false(&self) -> usize {
        self.f.count_ones()
    }
    pub fn count_unknown(&self) -> usize {
        self.unknown_set().count_ones()
    }

    /// Verify the partition invariant: TRUE/FALSE disjoint, both inside alive,
    /// and the three sets exhaust the alive universe exactly once.
    pub fn validate(&self) -> Result<()> {
        if self.t.len() != self.alive.len() || self.f.len() != self.alive.len() {
            return Err(Error::new(
                ErrorKind::UniverseMismatch,
                format!(
                    "triset bitmaps must share universe length: alive={} t={} f={}",
                    self.alive.len(),
                    self.t.len(),
                    self.f.len()
                ),
            ));
        }
        if !self.t.is_disjoint(&self.f) {
            return Err(Error::new(
                ErrorKind::InvalidInput,
                "TRUE and FALSE sets overlap; a row cannot be both",
            ));
        }
        // TRUE/FALSE must be subsets of alive (no deleted or tail-padding rows).
        let dead_t = self.t.and_not(&self.alive)?;
        let dead_f = self.f.and_not(&self.alive)?;
        if dead_t.count_ones() != 0 {
            return Err(Error::new(
                ErrorKind::DeletesetMismatch,
                format!(
                    "{} TRUE row(s) are outside the alive universe",
                    dead_t.count_ones()
                ),
            ));
        }
        if dead_f.count_ones() != 0 {
            return Err(Error::new(
                ErrorKind::DeletesetMismatch,
                format!(
                    "{} FALSE row(s) are outside the alive universe",
                    dead_f.count_ones()
                ),
            ));
        }
        // Exhaustiveness: t ∪ f ∪ u == alive (u = alive − t − f by construction).
        let u = self.unknown_set();
        let partition = self.t.or(&self.f)?.or(&u)?;
        if partition != self.alive {
            return Err(Error::new(
                ErrorKind::InvalidInput,
                "TRUE/FALSE/UNKNOWN do not exactly partition the alive universe",
            ));
        }
        Ok(())
    }

    /// Require two operands to share the same universe length AND alive set,
    /// i.e. compatible index/deletion modes for combination.
    fn check_compatible(&self, other: &Self) -> Result<()> {
        if self.alive.len() != other.alive.len() {
            return Err(Error::new(
                ErrorKind::UniverseMismatch,
                format!(
                    "universe lengths differ: {} vs {}",
                    self.alive.len(),
                    other.alive.len()
                ),
            ));
        }
        if self.alive != other.alive {
            return Err(Error::new(
                ErrorKind::DeletesetMismatch,
                "operands were built over different alive/deleted row sets",
            ));
        }
        Ok(())
    }

    /// SQL AND. FALSE if either side FALSE; TRUE only if both TRUE.
    pub fn and(&self, other: &Self) -> Result<Self> {
        self.check_compatible(other)?;
        let t = self.t.and(&other.t)?;
        // FALSE if at least one operand FALSE (a FALSE beats TRUE/UNKNOWN).
        let f = self.f.or(&other.f)?;
        Self::from_tf(self.alive.clone(), t, f)
    }

    /// SQL OR. TRUE if either side TRUE; FALSE only if both FALSE.
    pub fn or(&self, other: &Self) -> Result<Self> {
        self.check_compatible(other)?;
        let t = self.t.or(&other.t)?;
        let f = self.f.and(&other.f)?;
        Self::from_tf(self.alive.clone(), t, f)
    }

    /// SQL NOT: swap TRUE and FALSE; UNKNOWN stays UNKNOWN.
    ///
    /// New TRUE = old FALSE (already a subset of alive); new FALSE = old TRUE.
    /// We deliberately do NOT do `!true_bitmap`, which would mark deleted rows
    /// and tail-padding as TRUE.
    pub fn negate(&self) -> Result<Self> {
        // Re-validating makes the "computed against the alive universe" guarantee
        // explicit even though the swap alone preserves the invariant.
        Self::from_tf(self.alive.clone(), self.f.clone(), self.t.clone())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use Tri3::*;

    fn alive(n: usize) -> Bitmap {
        Bitmap::ones(n)
    }

    #[test]
    fn kleene_truth_table_matches_scalar_oracle() {
        use Tri3::*;
        assert_eq!(True.and(True), True);
        assert_eq!(True.and(False), False);
        assert_eq!(True.and(Unknown), Unknown);
        assert_eq!(False.and(Unknown), False);
        assert_eq!(Unknown.and(Unknown), Unknown);
        assert_eq!(True.or(False), True);
        assert_eq!(False.or(False), False);
        assert_eq!(False.or(Unknown), Unknown);
        assert_eq!(Unknown.or(True), True);
        assert_eq!(Unknown.negate(), Unknown);
        assert_eq!(True.negate(), False);
        assert_eq!(False.negate(), True);
    }

    #[test]
    fn not_never_includes_deleted_rows_or_tail_padding() {
        // Universe 13 (non-multiple of 8), row 3 deleted.
        let mut a = alive(13);
        a.set(3, false);
        // Predicate TRUE on rows 0,1,2 (and pretend-true nowhere else).
        let states = vec![
            True, True, True, Unknown, False, False, Unknown, True, False, Unknown, True, False,
            Unknown,
        ];
        let ts = TriSet::from_states(&a, &states).unwrap();
        let n = ts.negate().unwrap();
        // Deleted row 3 must not appear in ANY set.
        assert_eq!(n.state_at(3), None);
        // Tail positions 8..13 stay valid; no phantom bits beyond index 12.
        assert_eq!(
            n.true_set().count_ones() + n.false_set().count_ones() + n.count_unknown(),
            12
        );
        n.validate().unwrap();
    }

    #[test]
    fn combining_incompatible_deletesets_is_rejected() {
        let a1 = alive(10);
        let mut a2 = alive(10);
        a2.set(0, false);
        let x = TriSet::all_unknown(a1);
        let y = TriSet::all_unknown(a2);
        assert_eq!(x.and(&y).unwrap_err().kind, ErrorKind::DeletesetMismatch);
        assert_eq!(x.or(&y).unwrap_err().kind, ErrorKind::DeletesetMismatch);
    }

    #[test]
    fn every_alive_row_is_exactly_one_state() {
        let a = alive(11);
        let states = vec![
            True, False, Unknown, True, Unknown, False, True, Unknown, False, True, Unknown,
        ];
        let ts = TriSet::from_states(&a, &states).unwrap();
        assert_eq!(ts.count_true() + ts.count_false() + ts.count_unknown(), 11);
        assert_eq!(ts.count_true(), 4);
        assert_eq!(ts.count_false(), 3);
        assert_eq!(ts.count_unknown(), 4);
    }

    #[test]
    fn is_null_does_not_resurrect_deleted_rows() {
        let mut a = alive(9);
        a.set(2, false);
        let mut nulls = Bitmap::ones(9); // null everywhere including deleted row 2
        nulls.set(5, false);
        let ts = TriSet::is_null(&a, &nulls).unwrap();
        assert_eq!(ts.state_at(2), None); // deleted, not NULL
        assert_eq!(ts.state_at(5), Some(Tri3::False));
        assert_eq!(ts.count_true(), 7); // 8 alive rows minus row 5
    }
}
