//! Pure aggregation operators over *already sorted* keys.
//!
//! Nothing here performs I/O, grouping, or allocation batching — that lives in
//! [`crate::exec`]. These small state machines are the mathematical core and
//! are unit-tested directly against hand-computed samples.
//!
//! Semantics (documented and enforced separately from the SQL names):
//!
//! - **percentile_cont**: with `N` non-null values sorted ascending `v_0..`,
//!   `h = (N-1)*p`; result = `v_floor(h) + frac(h)*(v_ceil(h)-v_floor(h))`
//!   in `f64`. `p=0` is the minimum, `p=1` the maximum.
//! - **percentile_disc**: rank `ceil(p*N)` (1-based); the value at that rank,
//!   in the column's original type. `p=0` yields the minimum.
//! - NULLs are removed before sorting; they never occupy a rank.
//! - Tie order is the global, stable ingest ordinal, so equal keys never swap.

use std::cmp::Ordering;

/// Ordered measure key carried by the external sorter.
#[derive(Debug, Clone, PartialEq)]
pub enum SortKey {
    I64(i64),
    F64(f64),
    Utf8(String),
}

impl SortKey {
    /// Ascending key order, independent of ingest ordinal.
    /// f64 uses total order (`-0 == 0`, NaN sorts last).
    pub fn cmp_asc(&self, other: &Self) -> Ordering {
        match (self, other) {
            (SortKey::I64(a), SortKey::I64(b)) => a.cmp(b),
            (SortKey::F64(a), SortKey::F64(b)) => a.total_cmp(b),
            (SortKey::Utf8(a), SortKey::Utf8(b)) => a.cmp(b),
            _ => Ordering::Equal, // mixed kinds never occur within one operator
        }
    }
}

/// Returns true iff a key set contains any NaN (makes percentile undecidable).
pub fn contains_nan<'a, I>(keys: I) -> bool
where
    I: IntoIterator<Item = &'a SortKey>,
{
    keys.into_iter()
        .any(|k| matches!(k, SortKey::F64(v) if v.is_nan()))
}

/// Continuous percentile over ascending, NaN-free f64 values.
/// Returns `None` when there are zero non-null values (SQL NULL result).
pub fn percentile_cont(sorted: &[f64], p: f64) -> Option<f64> {
    let n = sorted.len();
    if n == 0 {
        return None;
    }
    if n == 1 {
        return Some(sorted[0]);
    }
    let h = (n as f64 - 1.0) * p;
    let lo = h.floor();
    let frac = h - lo;
    let lo_idx = lo as usize;
    let hi_idx = (lo_idx + 1).min(n - 1);
    let vlo = sorted[lo_idx];
    let vhi = sorted[hi_idx];
    Some(vlo + frac * (vhi - vlo))
}

/// Discrete percentile over any `Copy` numeric ascending slice.
/// Returns the value at 1-based rank `ceil(p*N)` (index `rank-1`).
pub fn percentile_disc<T: Copy>(sorted: &[T], p: f64) -> Option<T> {
    let n = sorted.len();
    if n == 0 {
        return None;
    }
    let idx = disc_target_index(n as u64, p);
    Some(sorted[idx])
}

/// Zero-based index selected by `percentile_disc`: rank `ceil(p*N)` pinned to
/// at least rank 1 for `p=0`.
pub fn disc_target_index(n: u64, p: f64) -> usize {
    let rank = (p * n as f64).ceil().max(1.0);
    (rank as usize)
        .saturating_sub(1)
        .min(n.saturating_sub(1) as usize)
}

/// Outcome of a mode aggregation.
#[derive(Debug, Clone, PartialEq)]
pub struct ModeOutcome<K> {
    /// Winner; when tied, the smallest key (deterministic tie break).
    pub winner: K,
    pub frequency: u64,
    /// True iff at least two distinct keys share `frequency`.
    pub tie: bool,
}

/// Compute mode from a slice already sorted ascending by key.
/// Equal keys are contiguous; a single linear pass suffices.
pub fn mode_sorted<K: Clone + PartialEq>(sorted: &[K]) -> Option<ModeOutcome<K>> {
    if sorted.is_empty() {
        return None;
    }
    let mut run_start = 0usize;
    let mut best_start = 0usize;
    let mut best_len = 0u64;
    let mut tie = false;
    let mut i = 1usize;
    while i <= sorted.len() {
        let run_ends = i == sorted.len() || sorted[i] != sorted[run_start];
        if run_ends {
            let run_len = (i - run_start) as u64;
            match run_len.cmp(&best_len) {
                Ordering::Greater => {
                    best_len = run_len;
                    best_start = run_start;
                    tie = false;
                }
                Ordering::Equal if best_len > 0 => {
                    // A second distinct key matches the best frequency.
                    // Keep the earlier (smaller, because sorted) winner.
                    tie = true;
                }
                _ => {}
            }
            run_start = i;
        }
        i += 1;
    }
    Some(ModeOutcome {
        winner: sorted[best_start].clone(),
        frequency: best_len,
        tie,
    })
}

/// Streaming ordered string concatenation for one group.
#[derive(Debug, Default)]
pub struct StringAggStream {
    buf: String,
    parts: u64,
}

impl StringAggStream {
    pub fn new() -> Self {
        Self::default()
    }

    /// Append one non-null value in sorted order. Returns the number of bytes
    /// the buffer grew by, so the executor can charge resident memory.
    pub fn push(&mut self, value: &str, delimiter: &str) -> usize {
        let before = self.buf.len();
        if self.parts > 0 {
            self.buf.push_str(delimiter);
        }
        self.buf.push_str(value);
        self.parts += 1;
        self.buf.len() - before
    }

    pub fn finish(self) -> String {
        self.buf
    }

    pub fn len(&self) -> usize {
        self.buf.len()
    }

    pub fn is_empty(&self) -> bool {
        self.parts == 0
    }
}

/// Streaming rank selector for percentile_cont over an ascending f64 feed.
pub struct ContSelector {
    n: u64,
    lo_idx: usize,
    hi_idx: usize,
    seen: usize,
    lo: Option<f64>,
    hi: Option<f64>,
}

impl ContSelector {
    /// Build after the non-null count `n` is known.
    pub fn new(n: u64, p: f64) -> Self {
        let h = ((n as f64) - 1.0) * p;
        let lo = h.floor();
        let lo_idx = lo as usize;
        let hi_idx = (lo_idx + 1).min(n.saturating_sub(1) as usize);
        Self {
            n,
            lo_idx,
            hi_idx,
            seen: 0,
            lo: None,
            hi: None,
        }
    }

    pub fn feed(&mut self, v: f64) {
        if self.seen == self.lo_idx {
            self.lo = Some(v);
        }
        if self.seen == self.hi_idx {
            self.hi = Some(v);
        }
        self.seen += 1;
    }

    pub fn finish(&self, p: f64) -> Option<f64> {
        if self.n == 0 {
            return None;
        }
        let vlo = self.lo?;
        let vhi = self.hi.unwrap_or(vlo);
        let h = (self.n as f64 - 1.0) * p;
        let frac = h - h.floor();
        Some(vlo + frac * (vhi - vlo))
    }
}

/// Streaming rank selector for percentile_disc over an ascending feed.
pub struct DiscSelector<T> {
    target_idx: usize,
    seen: usize,
    value: Option<T>,
    n: u64,
}

impl<T: Copy> DiscSelector<T> {
    pub fn new(n: u64, p: f64) -> Self {
        let rank = (p * n as f64).ceil().max(1.0);
        let target_idx = (rank as usize)
            .saturating_sub(1)
            .min(n.saturating_sub(1) as usize);
        Self {
            target_idx,
            seen: 0,
            value: None,
            n,
        }
    }

    pub fn feed(&mut self, v: T) {
        if self.seen == self.target_idx {
            self.value = Some(v);
        }
        self.seen += 1;
    }

    pub fn finish(&self) -> Option<T> {
        if self.n == 0 {
            None
        } else {
            self.value
        }
    }
}

/// First pass: count non-null keys and detect NaN in an ascending feed.
#[derive(Default, Debug, Clone)]
pub struct CountScanner {
    pub count: u64,
    pub has_nan: bool,
}

impl CountScanner {
    pub fn feed_f64(&mut self, v: f64) {
        self.count += 1;
        if v.is_nan() {
            self.has_nan = true;
        }
    }

    pub fn feed_key(&mut self, k: &SortKey) {
        match k {
            SortKey::F64(v) => self.feed_f64(*v),
            _ => self.count += 1,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Hand-computed on the even sample [10, 20, 30, 40] (N = 4).
    // h = (N-1) * p = 3p.
    #[test]
    fn cont_hand_computed_even_sample() {
        let v = [10.0, 20.0, 30.0, 40.0];
        assert_eq!(percentile_cont(&v, 0.0), Some(10.0));
        assert_eq!(percentile_cont(&v, 1.0), Some(40.0));
        // p = 0.5 -> h = 1.5 -> 20 + 0.5*(30-20) = 25
        assert_eq!(percentile_cont(&v, 0.5), Some(25.0));
        // p = 0.25 -> h = 0.75 -> 10 + 0.75*10 = 17.5
        assert_eq!(percentile_cont(&v, 0.25), Some(17.5));
        // p = 0.9 -> h = 2.7 -> 30 + 0.7*10 = 37
        assert_eq!(percentile_cont(&v, 0.9), Some(37.0));
    }

    #[test]
    fn cont_single_and_empty() {
        assert_eq!(percentile_cont(&[], 0.5), None);
        assert_eq!(percentile_cont(&[7.5], 0.0), Some(7.5));
        assert_eq!(percentile_cont(&[7.5], 1.0), Some(7.5));
    }

    // Disc uses rank ceil(p*N) on [10, 20, 30, 40].
    #[test]
    fn disc_hand_computed_even_sample() {
        let v = [10_i64, 20, 30, 40];
        assert_eq!(percentile_disc(&v, 0.0), Some(10));
        // p=0.5 -> rank 2 -> 20
        assert_eq!(percentile_disc(&v, 0.5), Some(20));
        // p=0.51 -> rank ceil(2.04)=3 -> 30
        assert_eq!(percentile_disc(&v, 0.51), Some(30));
        assert_eq!(percentile_disc(&v, 1.0), Some(40));
    }

    #[test]
    fn mode_unique_winner_and_empty() {
        let m = mode_sorted(&[1, 1, 2, 2, 2, 3]).unwrap();
        assert_eq!(m.winner, 2);
        assert_eq!(m.frequency, 3);
        assert!(!m.tie);
        assert!(mode_sorted::<i64>(&[]).is_none());
    }

    // Equal-frequency tie: smallest key wins, tie flag set.
    #[test]
    fn mode_tie_smallest_wins() {
        let m = mode_sorted(&["apple", "apple", "pear", "pear"]).unwrap();
        assert_eq!(m.winner, "apple");
        assert_eq!(m.frequency, 2);
        assert!(m.tie);
    }

    #[test]
    fn mode_three_way_tie() {
        let m = mode_sorted(&[5, 5, 7, 7, 9, 9]).unwrap();
        assert_eq!(m.winner, 5);
        assert!(m.tie);
        assert_eq!(m.frequency, 2);
    }

    #[test]
    fn string_agg_stable_order_and_delimiter() {
        let mut s = StringAggStream::new();
        let _ = s.push("b", ",");
        let _ = s.push("a", ",");
        let _ = s.push("c", "|");
        assert_eq!(s.finish(), "b,a|c");
    }

    #[test]
    fn selectors_pick_only_needed_ranks() {
        let mut c = ContSelector::new(4, 0.5);
        for v in [10.0, 20.0, 30.0, 40.0] {
            c.feed(v);
        }
        assert_eq!(c.finish(0.5), Some(25.0));

        let mut d = DiscSelector::<i64>::new(4, 0.5);
        for v in [10, 20, 30, 40] {
            d.feed(v);
        }
        assert_eq!(d.finish(), Some(20));
    }
}
