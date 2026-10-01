//! Exact aggregation primitives.
//!
//! The continuous and discrete percentile definitions are kept deliberately
//! separate, mirroring SQL's `PERCENTILE_CONT` / `PERCENTILE_DISC`:
//!
//! * [`percentile_cont`] — linear interpolation between the two order
//!   statistics bracketing rank `q * (n - 1)` (0-based). Always returns
//!   `f64`; nulls must already be removed.
//! * [`percentile_disc_index`] — index of the first value whose cumulative
//!   distribution is `>= q`, i.e. `ceil(q * n)` in 1-based rank terms. The
//!   caller keeps the original typed value, so the result preserves the input
//!   type (int64 stays int64).
//!
//! [`mode_sorted`] returns the most frequent value; ties are resolved towards
//! the smallest value (the slice must be ascending), matching PostgreSQL.
//!
//! The streaming `*Selector` types below let the finalize operator compute the
//! same answers from the sorted merge stream without buffering a group: the
//! merge makes a second pass that counts non-null rows first, so the
//! percentiles know `n` before the selecting pass runs.

use crate::exec::cells::Cell;

/// `PERCENTILE_CONT` over an ascending slice of non-null numeric values.
///
/// Hand-checked example: `[1,2,3,4]`, `q = 0.5` → rank 1.5 →
/// `2 + 0.5*(3-2) = 2.5`.
pub fn percentile_cont(sorted_nonnull: &[f64], q: f64) -> f64 {
    let n = sorted_nonnull.len();
    assert!(n >= 1, "percentile_cont requires at least one value");
    if n == 1 {
        return sorted_nonnull[0];
    }
    let rank = q * (n as f64 - 1.0);
    let lower = rank.floor();
    let upper = rank.ceil();
    let frac = rank - lower;
    let lo = sorted_nonnull[lower as usize];
    let hi = sorted_nonnull[upper as usize];
    lo + frac * (hi - lo)
}

/// 0-based index that `PERCENTILE_DISC` selects for `n` non-null values.
///
/// `q = 0` clamps to the first value; otherwise the index is `ceil(q*n) - 1`.
pub fn percentile_disc_index(n: usize, q: f64) -> usize {
    assert!(n >= 1, "percentile_disc requires at least one value");
    let rank = (q * n as f64).ceil() as usize;
    rank.max(1) - 1
}

/// Most frequent value in an ascending slice; ties resolve to the smallest.
pub fn mode_sorted(sorted_nonnull: &[Cell]) -> Option<Cell> {
    let mut best: Option<&Cell> = None;
    let mut best_count = 0usize;
    let mut i = 0;
    while i < sorted_nonnull.len() {
        let value = &sorted_nonnull[i];
        let run_end = sorted_nonnull[i..]
            .iter()
            .position(|v| v != value)
            .map(|p| i + p)
            .unwrap_or(sorted_nonnull.len());
        let count = run_end - i;
        // Strictly greater only: an equal-frequency later (hence larger) value
        // never replaces the earlier smaller one.
        if count > best_count {
            best_count = count;
            best = Some(value);
        }
        i = run_end;
    }
    best.cloned()
}

/// Join non-null strings in slice order.
pub fn string_agg_in_order<'a, I>(values: I, delimiter: &str) -> Option<String>
where
    I: IntoIterator<Item = &'a Cell>,
{
    let mut out: Option<String> = None;
    for value in values {
        if let Cell::Str(s) = value {
            match out.as_mut() {
                None => out = Some(s.clone()),
                Some(buf) => {
                    buf.push_str(delimiter);
                    buf.push_str(s);
                }
            }
        }
    }
    out
}

// ---------------------------------------------------------------------------
// Streaming selectors used by the finalize operator.
// ---------------------------------------------------------------------------

/// Captures the two bracketing values while a sorted group streams by, once
/// the non-null count is known.
pub struct ContSelector {
    n: usize,
    lo_idx: usize,
    hi_idx: usize,
    frac: f64,
    seen: usize,
    lo: Option<f64>,
    hi: Option<f64>,
}

impl ContSelector {
    pub fn new(n: usize, q: f64) -> Self {
        let rank = if n <= 1 { 0.0 } else { q * (n as f64 - 1.0) };
        let lo_idx = rank.floor() as usize;
        let hi_idx = rank.ceil() as usize;
        Self {
            n,
            lo_idx,
            hi_idx,
            frac: rank - rank.floor(),
            seen: 0,
            lo: None,
            hi: None,
        }
    }

    /// Feed one cell; nulls are skipped (they sort first in the stream).
    pub fn push(&mut self, cell: &Cell) {
        let Some(v) = numeric(cell) else { return };
        if self.seen == self.lo_idx {
            self.lo = Some(v);
        }
        if self.seen == self.hi_idx {
            self.hi = Some(v);
        }
        self.seen += 1;
    }

    pub fn finish(self) -> Option<Cell> {
        if self.n == 0 {
            return None;
        }
        let lo = self.lo.expect("cont selector captured lower value");
        let hi = self.hi.expect("cont selector captured upper value");
        Some(Cell::F64(lo + self.frac * (hi - lo)))
    }
}

/// Captures the typed value at the discrete percentile index.
pub struct DiscSelector {
    target: usize,
    seen: usize,
    picked: Option<Cell>,
}

impl DiscSelector {
    pub fn new(n: usize, q: f64) -> Self {
        Self {
            target: percentile_disc_index(n.max(1), q),
            seen: 0,
            picked: None,
        }
    }

    pub fn push(&mut self, cell: &Cell) {
        if cell.is_null() {
            return;
        }
        if self.seen == self.target && self.picked.is_none() {
            self.picked = Some(cell.clone());
        }
        self.seen += 1;
    }

    pub fn finish(self) -> Option<Cell> {
        self.picked
    }
}

/// Streaming mode via run-length encoding over the sorted group.
#[derive(Default)]
pub struct ModeSelector {
    current: Option<Cell>,
    run: usize,
    best: Option<Cell>,
    best_run: usize,
}

impl ModeSelector {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn push(&mut self, cell: &Cell) {
        if cell.is_null() {
            return;
        }
        if self.current.as_ref() == Some(cell) {
            self.run += 1;
        } else {
            self.flush();
            self.current = Some(cell.clone());
            self.run = 1;
        }
    }

    fn flush(&mut self) {
        if self.run > self.best_run {
            self.best_run = self.run;
            self.best = self.current.clone();
        }
    }

    pub fn finish(mut self) -> Option<Cell> {
        self.flush();
        self.best
    }
}

/// Incremental ordered string join.
pub struct StringAggSelector {
    delimiter: String,
    buf: Option<String>,
}

impl StringAggSelector {
    pub fn new(delimiter: String) -> Self {
        Self {
            delimiter,
            buf: None,
        }
    }

    pub fn push(&mut self, cell: &Cell) {
        if let Cell::Str(s) = cell {
            match self.buf.as_mut() {
                None => self.buf = Some(s.clone()),
                Some(buf) => {
                    buf.push_str(&self.delimiter);
                    buf.push_str(s);
                }
            }
        }
    }

    pub fn finish(self) -> Option<Cell> {
        self.buf.map(Cell::Str)
    }
}

fn numeric(cell: &Cell) -> Option<f64> {
    match cell {
        Cell::I64(v) => Some(*v as f64),
        Cell::F64(v) => Some(*v),
        Cell::Null | Cell::Str(_) => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fcells(xs: &[f64]) -> Vec<Cell> {
        xs.iter().map(|v| Cell::F64(*v)).collect()
    }

    #[test]
    fn cont_hand_computed_even_sample() {
        // Reference: median of {1,2,3,4} is 2.5.
        let vals = fcells(&[1.0, 2.0, 3.0, 4.0]);
        let nums: Vec<f64> = vals.iter().filter_map(numeric).collect();
        assert_eq!(percentile_cont(&nums, 0.5), 2.5);
        // q=0.25 on the same sample: rank 0.75 → 1 + 0.75 = 1.75.
        assert_eq!(percentile_cont(&nums, 0.25), 1.75);
        // Endpoints.
        assert_eq!(percentile_cont(&nums, 0.0), 1.0);
        assert_eq!(percentile_cont(&nums, 1.0), 4.0);
    }

    #[test]
    fn cont_streaming_matches_slice_formula() {
        let vals = fcells(&[2.0, 4.0, 6.0, 8.0, 10.0]);
        for q in [0.0, 0.1, 0.37, 0.5, 0.9, 1.0] {
            let expected = percentile_cont(&[2.0, 4.0, 6.0, 8.0, 10.0], q);
            let mut sel = ContSelector::new(5, q);
            vals.iter().for_each(|c| sel.push(c));
            assert_eq!(sel.finish(), Some(Cell::F64(expected)), "q={q}");
        }
    }

    #[test]
    fn disc_matches_ceil_rank_and_preserves_type() {
        // n=4: q=0.5 -> rank ceil(2)=2 -> index 1 (value 20).
        let vals = [Cell::I64(10), Cell::I64(20), Cell::I64(30), Cell::I64(40)];
        assert_eq!(percentile_disc_index(4, 0.5), 1);
        let mut sel = DiscSelector::new(4, 0.5);
        vals.iter().for_each(|c| sel.push(c));
        assert_eq!(sel.finish(), Some(Cell::I64(20)));
        // q=0 -> smallest, q=1 -> largest.
        assert_eq!(percentile_disc_index(4, 0.0), 0);
        assert_eq!(percentile_disc_index(4, 1.0), 3);
    }

    #[test]
    fn disc_skips_nulls_and_cont_skips_nulls() {
        let vals = [
            Cell::Null,
            Cell::F64(1.0),
            Cell::F64(2.0),
            Cell::Null,
            Cell::F64(3.0),
        ];
        let mut disc = DiscSelector::new(3, 0.5); // ceil(1.5)=2 -> index 1 -> 2.0
        vals.iter().for_each(|c| disc.push(c));
        assert_eq!(disc.finish(), Some(Cell::F64(2.0)));

        let mut cont = ContSelector::new(3, 0.5); // rank 1 -> exactly 2.0
        vals.iter().for_each(|c| cont.push(c));
        assert_eq!(cont.finish(), Some(Cell::F64(2.0)));
    }

    #[test]
    fn all_null_group_yields_none() {
        let vals = [Cell::Null, Cell::Null];
        let mut sel = ContSelector::new(0, 0.5);
        vals.iter().for_each(|c| sel.push(c));
        assert_eq!(sel.finish(), None);
        assert_eq!(ModeSelector::new().finish(), None);
        assert_eq!(StringAggSelector::new(",".into()).finish(), None);
    }

    #[test]
    fn mode_tie_picks_smallest_and_large_repeat_group_wins() {
        let tied = vec![
            Cell::Str("a".into()),
            Cell::Str("a".into()),
            Cell::Str("b".into()),
            Cell::Str("b".into()),
        ];
        assert_eq!(mode_sorted(&tied), Some(Cell::Str("a".into())));

        let mut streaming = ModeSelector::new();
        tied.iter().for_each(|c| streaming.push(c));
        assert_eq!(streaming.finish(), Some(Cell::Str("a".into())));

        let big: Vec<Cell> = (0..100)
            .map(|_| Cell::Str("z".into()))
            .chain((0..3).map(|_| Cell::Str("a".into())))
            .collect();
        assert_eq!(mode_sorted(&big), Some(Cell::Str("z".into())));
    }

    #[test]
    fn string_agg_respects_order_and_delimiter_and_ignores_nulls() {
        let asc = [
            Cell::Str("a".into()),
            Cell::Null,
            Cell::Str("b".into()),
            Cell::Str("c".into()),
        ];
        assert_eq!(
            string_agg_in_order(asc.iter(), "|").as_deref(),
            Some("a|b|c")
        );
        let mut sel = StringAggSelector::new("-".into());
        [Cell::Str("x".into()), Cell::Str("y".into())]
            .iter()
            .for_each(|c| sel.push(c));
        assert_eq!(sel.finish(), Some(Cell::Str("x-y".into())));
    }
}
