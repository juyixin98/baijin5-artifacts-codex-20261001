//! Sort permutations and position maps.
//!
//! IEJoin never physically reorders rows; it builds:
//!
//! * an **order permutation** `order` — original row indices visited in
//!   key order (ascending for `<`/`<=`, descending for `>`/`>=`),
//! * a **position map** `position[original_row]` — where that row lives
//!   in the permutation. NULL rows map to `None` and are never indexed.
//!
//! Ties are broken by the original row index, so the permutation is
//! total and deterministic even when every key is equal. Rows sharing a
//! key form an explicit **equality group**: strict predicates must stop
//! just below the group, non-strict predicates may enter it. Activating
//! a whole group in one update is what guarantees the bitmap never
//! misses equal values (or admits them one boundary too early).

use crate::error::{JoinError, JoinResult};

/// Sort sense + strictness for one side of a range predicate.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SortSpec {
    /// true => `<` / `<=` (ascending keys), false => `>` / `>=`.
    pub ascending: bool,
    /// true => strict (`<` / `>`); false => `<=` / `>=`.
    pub strict: bool,
}

impl SortSpec {
    #[must_use]
    pub fn satisfies(self, left_key: i64, right_key: i64) -> bool {
        match (self.ascending, self.strict) {
            (true, true) => left_key < right_key,
            (true, false) => left_key <= right_key,
            (false, true) => left_key > right_key,
            (false, false) => left_key >= right_key,
        }
    }
}

/// A total ordering of the non-NULL rows of one column.
#[derive(Clone, Debug)]
pub struct Permutation {
    /// `order[k]` = original row index at permutation position k.
    pub order: Vec<u32>,
    /// Sorted keys, parallel to `order`.
    pub keys: Vec<i64>,
    /// Position map: original row -> permutation position, NULL => None.
    pub position: Vec<Option<u32>>,
    pub ascending: bool,
}

impl Permutation {
    /// Build the permutation of `col` under the requested sense.
    ///
    /// # Errors
    /// Propagates structural errors only (currently infallible, kept in
    /// the contract for future typed columns).
    pub fn build(col: &[Option<i64>], ascending: bool) -> JoinResult<Self> {
        let mut idx: Vec<u32> = (0..col.len())
            .filter(|&i| col[i].is_some())
            .map(|i| i as u32)
            .collect();
        // Stable sort: ties keep original order, giving a total order.
        idx.sort_by(|&a, &b| {
            let va = col[a as usize].expect("filtered non-null");
            let vb = col[b as usize].expect("filtered non-null");
            let ord = va.cmp(&vb);
            if ascending {
                ord.then_with(|| a.cmp(&b))
            } else {
                ord.reverse().then_with(|| a.cmp(&b))
            }
        });
        let keys = idx
            .iter()
            .map(|&i| col[i as usize].expect("filtered non-null"))
            .collect();
        let mut position = vec![None; col.len()];
        for (p, &row) in idx.iter().enumerate() {
            position[row as usize] = Some(p as u32);
        }
        Ok(Self {
            order: idx,
            keys,
            position,
            ascending,
        })
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.order.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.order.is_empty()
    }

    /// Number of leading permutation rows whose key satisfies
    /// `key CMP value` under `spec`. This is the strict/non-strict
    /// boundary: with strict comparison the whole equal-value group is
    /// excluded; with `<=`/`>=` it is included.
    #[must_use]
    pub fn satisfied_prefix(&self, value: i64, spec: SortSpec) -> usize {
        self.satisfied_prefix_probed(value, spec).0
    }

    /// Same as [`Self::satisfied_prefix`], also returning how many key
    /// comparisons the binary search made — a measure of the work used
    /// to locate the predicate-2 boundary.
    #[must_use]
    pub fn satisfied_prefix_probed(&self, value: i64, spec: SortSpec) -> (usize, u64) {
        if self.ascending != spec.ascending {
            // Defensive: callers always build permutations to match.
            return (0, 0);
        }
        let ks = &self.keys;
        let (idx, probes) = match (spec.ascending, spec.strict) {
            // first index where !(key < value), i.e. key >= value
            (true, true) => partition_point_probed(ks, |&k| k < value),
            // first index where !(key <= value), i.e. key > value
            (true, false) => partition_point_probed(ks, |&k| k <= value),
            // descending: first index where !(key > value), i.e. key <= value
            (false, true) => partition_point_probed(ks, |&k| k > value),
            (false, false) => partition_point_probed(ks, |&k| k >= value),
        };
        (idx, probes)
    }

    /// Original-row indices of the equality group occupying
    /// permutation positions `[start, end)`.
    #[must_use]
    pub fn group_rows(&self, start: usize, end: usize) -> &[u32] {
        &self.order[start..end]
    }
}

/// Binary search with the same semantics as
/// [`slice::partition_point`], also reporting the number of predicate
/// evaluations (key comparisons) it performed.
fn partition_point_probed<P>(xs: &[i64], mut pred: P) -> (usize, u64)
where
    P: FnMut(&i64) -> bool,
{
    let mut lo = 0usize;
    let mut hi = xs.len();
    let mut probes = 0u64;
    while lo < hi {
        let mid = lo + (hi - lo) / 2;
        probes += 1;
        if pred(&xs[mid]) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    (lo, probes)
}

/// Cursor that walks a [`Permutation`] monotonically as right-hand
/// values arrive in the same order, activating whole equality groups.
///
/// The cursor is detached from the permutation (it stores only the
/// boundary position) so it can live inside a long-lived runner while
/// the permutation is borrowed elsewhere; pass the permutation to
/// [`GateCursor::advance`].
///
/// `activated` is a monotone prefix of the permutation; each call moves
/// the boundary to the largest prefix satisfying the predicate against
/// `right_value`, and returns the newly activated *original row*
/// indices.
#[derive(Clone, Debug)]
pub struct GateCursor {
    spec: SortSpec,
    cursor: usize,
    visits: u64,
}

impl GateCursor {
    #[must_use]
    pub fn new(spec: SortSpec) -> Self {
        Self {
            spec,
            cursor: 0,
            visits: 0,
        }
    }

    /// Rebuild a cursor at a known boundary (resume from checkpoint).
    #[must_use]
    pub fn at(spec: SortSpec, cursor: usize) -> Self {
        Self {
            spec,
            cursor,
            visits: 0,
        }
    }

    #[must_use]
    pub fn position(&self) -> usize {
        self.cursor
    }

    /// Advance the activation boundary for a right-hand value.
    /// Returns original row indices newly activated. The walk is
    /// group-aware: an equal-keyed group is crossed as a unit only when
    /// the predicate admits it, so a strict boundary never splits a
    /// group and a non-strict one never lags behind it.
    pub fn advance(&mut self, perm: &Permutation, right_value: i64) -> Vec<u32> {
        debug_assert_eq!(perm.ascending, self.spec.ascending);
        let target = perm.satisfied_prefix(right_value, self.spec);
        let mut activated = Vec::with_capacity(target.saturating_sub(self.cursor));
        while self.cursor < target {
            // Find the end of the equality group starting at cursor.
            let key = perm.keys[self.cursor];
            let mut group_end = self.cursor + 1;
            while group_end < perm.keys.len() && perm.keys[group_end] == key {
                group_end += 1;
                self.visits += 1;
            }
            // A group can only be activated wholesale: target either
            // admits the full group (boundary is at/after group_end) or,
            // for a strict predicate, stops exactly before it.
            if group_end > target {
                // Strict boundary inside this group -> stop at its start.
                debug_assert!(self.spec.strict);
                break;
            }
            for &row in &perm.order[self.cursor..group_end] {
                activated.push(row);
            }
            self.visits += (group_end - self.cursor) as u64;
            self.cursor = group_end;
        }
        activated
    }

    #[must_use]
    pub fn visits(&self) -> u64 {
        self.visits
    }
}

/// Validate a column exists and return its raw slice.
///
/// # Errors
/// `Input` when the column is absent.
pub(crate) fn require_col<'b>(
    cols: &'b [crate::batch::Column],
    name: &str,
) -> JoinResult<&'b [Option<i64>]> {
    cols.iter()
        .find(|c| c.name == name)
        .map(|c| c.values.as_slice())
        .ok_or_else(|| JoinError::input("unknown_column", format!("key column '{name}' not found")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strict_and_lenient_boundaries_differ_on_equals() {
        let col = vec![Some(1), Some(2), Some(2), Some(3)];
        let p = Permutation::build(&col, true).unwrap();
        assert_eq!(
            p.satisfied_prefix(
                2,
                SortSpec {
                    ascending: true,
                    strict: true
                }
            ),
            1
        );
        assert_eq!(
            p.satisfied_prefix(
                2,
                SortSpec {
                    ascending: true,
                    strict: false
                }
            ),
            3
        );
    }

    #[test]
    fn nulls_are_outside_the_permutation() {
        let col = vec![None, Some(5), None, Some(1)];
        let p = Permutation::build(&col, true).unwrap();
        assert_eq!(p.len(), 2);
        assert_eq!(p.order, vec![3, 1]);
        assert_eq!(p.position[0], None);
        assert_eq!(p.position[2], None);
        assert_eq!(p.position[3], Some(0));
    }

    #[test]
    fn gate_activates_equal_groups_together() {
        let col = vec![Some(1), Some(2), Some(2), Some(3)];
        let p = Permutation::build(&col, true).unwrap();

        // strict <, right value 2 -> only the key=1 row.
        let mut g = GateCursor::new(SortSpec {
            ascending: true,
            strict: true,
        });
        assert_eq!(g.advance(&p, 2), vec![0]);
        // next right value 2 again activates nothing new (group stable).
        assert!(g.advance(&p, 2).is_empty());
        // right value 3 jumps the whole key=2 group at once.
        let mut on3 = g.advance(&p, 3);
        on3.sort_unstable();
        assert_eq!(on3, vec![1, 2]);

        // non-strict <= at value 2 admits the group immediately.
        let mut ge = GateCursor::new(SortSpec {
            ascending: true,
            strict: false,
        });
        let mut v = ge.advance(&p, 2);
        v.sort_unstable();
        assert_eq!(v, vec![0, 1, 2]);
    }

    #[test]
    fn descending_gate_handles_greater() {
        let col = vec![Some(1), Some(2), Some(3)];
        let p = Permutation::build(&col, false).unwrap();
        assert_eq!(p.keys, vec![3, 2, 1]);
        let mut g = GateCursor::new(SortSpec {
            ascending: false,
            strict: true,
        });
        let mut v = g.advance(&p, 2); // left > 2 -> only key 3
        v.sort_unstable();
        assert_eq!(v, vec![2]);
    }
}
