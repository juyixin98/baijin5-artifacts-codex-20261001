//! Ordered Tries with explicit multiplicity and Leapfrog-style cursors.
//!
//! Every relation is turned into a Trie over a chosen column permutation:
//!
//! - level 0 holds the distinct values of the first column, sorted;
//! - level `d+1` groups, for each node at level `d`, the distinct values of
//!   the next column that occur under that prefix;
//! - each node carries `count`: the number of *source rows* matching the
//!   prefix. At the leaf this is the multiplicity of the full row, so
//!   duplicate input rows are preserved rather than collapsed.
//!
//! Level arrays are flat (`values[d]`, `counts[d]` plus child ranges) which
//! keeps sibling sets contiguous and binary-searchable. A [`TrieCursor`]
//! walks the Trie and counts every access (seeks, comparisons, nexts, opened
//! child nodes); the engine reports those counters so tests can prove the
//! difference between Leapfrog and naive enumeration.

use std::cmp::Ordering;

use serde::{Deserialize, Serialize};

use crate::value::Scalar;

/// Trie key: NULL sorts before any present value.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TrieKey(pub Option<Scalar>);

impl PartialOrd for TrieKey {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for TrieKey {
    fn cmp(&self, other: &Self) -> Ordering {
        match (&self.0, &other.0) {
            (None, None) => Ordering::Equal,
            (None, Some(_)) => Ordering::Less,
            (Some(_), None) => Ordering::Greater,
            (Some(a), Some(b)) => a.cmp(b),
        }
    }
}

/// Flat, fully materialised ordered Trie for one relation/atom.
#[derive(Debug, Clone)]
pub struct Trie {
    /// Source-column index for each trie level.
    pub permutation: Vec<usize>,
    values: Vec<Vec<TrieKey>>,
    counts: Vec<Vec<u64>>,
    /// Child range into level `d+1`, per node at level `d` (empty at leaf).
    child_lo: Vec<Vec<u32>>,
    child_hi: Vec<Vec<u32>>,
    total_rows: u64,
}

impl Trie {
    pub fn arity(&self) -> usize {
        self.permutation.len()
    }

    pub fn total_rows(&self) -> u64 {
        self.total_rows
    }

    /// Number of distinct nodes at a level (root level = distinct first keys).
    pub fn level_size(&self, level: usize) -> usize {
        self.values[level].len()
    }

    /// Build from projected rows. Each input row must already have length
    /// equal to `permutation.len()`; rows are a multiset (duplicates allowed).
    pub fn build(permutation: Vec<usize>, projected: Vec<Vec<TrieKey>>) -> Self {
        debug_assert!(!permutation.is_empty());
        let arity = permutation.len();
        let total_rows = projected.len() as u64;

        let mut sorted = projected;
        // Deterministic lexicographic order; NULL-first via TrieKey's Ord.
        sorted.sort_unstable();

        let mut values: Vec<Vec<TrieKey>> = vec![Vec::new(); arity];
        let mut counts: Vec<Vec<u64>> = vec![Vec::new(); arity];
        let mut child_lo: Vec<Vec<u32>> = vec![Vec::new(); arity];
        let mut child_hi: Vec<Vec<u32>> = vec![Vec::new(); arity];

        // Walk the sorted row multiset level by level. Rows sharing a prefix
        // are contiguous, so one recursive pass yields the child ranges and a
        // second one derives each node's multiplicity from its run length.
        build_layout(0, &sorted, &mut values, &mut child_lo, &mut child_hi);
        counts_from_groups(0, arity, &sorted, &mut counts);

        Trie {
            permutation,
            values,
            counts,
            child_lo,
            child_hi,
            total_rows,
        }
    }
}

/// Recursively emit distinct nodes per level plus child ranges.
fn build_layout(
    depth: usize,
    rows: &[Vec<TrieKey>],
    values: &mut [Vec<TrieKey>],
    child_lo: &mut [Vec<u32>],
    child_hi: &mut [Vec<u32>],
) {
    let mut start = 0;
    while start < rows.len() {
        let key = rows[start][depth].clone();
        let mut end = start + 1;
        while end < rows.len() && rows[end][depth] == key {
            end += 1;
        }
        values[depth].push(key);
        if depth + 1 < values.len() {
            let lo = values[depth + 1].len() as u32;
            build_layout(depth + 1, &rows[start..end], values, child_lo, child_hi);
            let hi = values[depth + 1].len() as u32;
            child_lo[depth].push(lo);
            child_hi[depth].push(hi);
        }
        start = end;
    }
}

/// Fill `counts[d]` with, per node, the number of source rows below it
/// (leaf nodes therefore carry full-row multiplicity).
fn counts_from_groups(depth: usize, arity: usize, rows: &[Vec<TrieKey>], counts: &mut [Vec<u64>]) {
    let mut start = 0;
    while start < rows.len() {
        let mut end = start + 1;
        while end < rows.len() && rows[end][depth] == rows[start][depth] {
            end += 1;
        }
        counts[depth].push((end - start) as u64);
        if depth + 1 < arity {
            counts_from_groups(depth + 1, arity, &rows[start..end], counts);
        }
        start = end;
    }
}

/// Mutable access counters shared by every cursor of one engine run.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct AccessCounters {
    /// Number of `seek` invocations.
    pub seeks: u64,
    /// Key comparisons performed inside binary-search seeks.
    pub seek_comparisons: u64,
    /// Linear `next` advances.
    pub nexts: u64,
    /// Times a child level was opened under a bound prefix.
    pub child_opens: u64,
}

impl AccessCounters {
    pub fn total(&self) -> u64 {
        self.seeks + self.seek_comparisons + self.nexts + self.child_opens
    }
}

/// One entry of the cursor navigation stack.
#[derive(Debug, Clone, PartialEq, Eq)]
struct Frame {
    lo: u32,
    hi: u32,
    idx: u32,
}

/// Read-only Leapfrog cursor over one [`Trie`]. Each cursor owns its own
/// counters; the engine merges them after a run.
pub struct TrieCursor<'a> {
    trie: &'a Trie,
    level: isize,
    lo: u32,
    hi: u32,
    idx: u32,
    stack: Vec<Frame>,
    counters: AccessCounters,
}

impl<'a> TrieCursor<'a> {
    pub fn new(trie: &'a Trie) -> Self {
        Self {
            trie,
            level: -1,
            lo: 0,
            hi: 0,
            idx: 0,
            stack: Vec::new(),
            counters: AccessCounters::default(),
        }
    }

    /// Access counters accumulated so far.
    pub fn counters(&self) -> &AccessCounters {
        &self.counters
    }

    /// Merge another counter set into this one.
    pub fn merge_counters(&mut self, other: &AccessCounters) {
        self.counters.seeks += other.seeks;
        self.counters.seek_comparisons += other.seek_comparisons;
        self.counters.nexts += other.nexts;
        self.counters.child_opens += other.child_opens;
    }

    pub fn arity(&self) -> usize {
        self.trie.arity()
    }

    /// Position at level 0 on the first root key (or at end when empty).
    pub fn open_root(&mut self) {
        debug_assert_eq!(self.level, -1, "root opened twice without reset/up");
        self.level = 0;
        self.lo = 0;
        self.hi = self.trie.values[0].len() as u32;
        self.idx = 0;
        self.counters.child_opens += 1;
    }

    pub fn reset(&mut self) {
        self.level = -1;
        self.stack.clear();
    }

    /// Descend one level under the current node, positioned at its first
    /// child key.
    pub fn open_child(&mut self) {
        let level = self.level as usize;
        debug_assert!(level + 1 < self.trie.arity(), "open_child past leaf");
        let parent = self.idx;
        self.stack.push(Frame {
            lo: self.lo,
            hi: self.hi,
            idx: self.idx,
        });
        self.level += 1;
        self.lo = self.trie.child_lo[level][parent as usize];
        self.hi = self.trie.child_hi[level][parent as usize];
        self.idx = self.lo;
        self.counters.child_opens += 1;
    }

    pub fn up(&mut self) {
        let frame = self.stack.pop().expect("up() without open_child()");
        self.level -= 1;
        self.lo = frame.lo;
        self.hi = frame.hi;
        self.idx = frame.idx;
    }

    /// Current trie level (-1 before [`open_root`](Self::open_root)).
    pub fn level(&self) -> isize {
        self.level
    }

    pub fn at_end(&self) -> bool {
        self.idx >= self.hi
    }

    pub fn key(&self) -> &TrieKey {
        assert!(!self.at_end(), "key() at end");
        &self.trie.values[self.level as usize][self.idx as usize]
    }

    /// Multiplicity of the current node (prefix rows, or full-row mult at leaf).
    pub fn count(&self) -> u64 {
        self.trie.counts[self.level as usize][self.idx as usize]
    }

    /// Remaining keys in the current sibling range incl. current key.
    pub fn remaining(&self) -> u32 {
        self.hi.saturating_sub(self.idx)
    }

    /// Advance to the next sibling key.
    pub fn next(&mut self) {
        if self.idx < self.hi {
            self.idx += 1;
            self.counters.nexts += 1;
        }
    }

    /// Advance within the current sibling range to the first key `>= target`.
    /// Binary search: never skips a key that could belong to the intersection.
    pub fn seek(&mut self, target: &TrieKey) {
        self.counters.seeks += 1;
        let level = self.level as usize;
        let vals = &self.trie.values[level];
        let base = self.idx as usize;
        let slice = &vals[base..self.hi as usize];
        // Partition point via explicit binary search so comparisons counted.
        let mut left = 0usize;
        let mut right = slice.len();
        while left < right {
            let mid = left + (right - left) / 2;
            self.counters.seek_comparisons += 1;
            if slice[mid] < *target {
                left = mid + 1;
            } else {
                right = mid;
            }
        }
        self.idx = (base + left) as u32;
    }

    /// Rebind a saved path during query resumption: open root/children and
    /// seek each level to `path[level]`. Returns false if the path vanished.
    pub fn rebind_path(&mut self, path: &[TrieKey]) -> bool {
        self.reset();
        if path.is_empty() {
            return true;
        }
        self.open_root();
        self.seek(&path[0]);
        if self.at_end() || self.key() != &path[0] {
            return false;
        }
        for key in &path[1..] {
            self.open_child();
            self.seek(key);
            if self.at_end() || self.key() != key {
                return false;
            }
        }
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use Scalar::Int;

    fn k(i: i64) -> TrieKey {
        TrieKey(Some(Int(i)))
    }

    #[test]
    fn builds_sorted_levels_with_leaf_multiplicity() {
        // relation (a,b): (1,10) twice, (1,12), (2,10)
        let rows = vec![
            vec![k(2), k(10)],
            vec![k(1), k(10)],
            vec![k(1), k(10)],
            vec![k(1), k(12)],
        ];
        let trie = Trie::build(vec![0, 1], rows);

        assert_eq!(trie.level_size(0), 2);
        assert_eq!(trie.values[0][0], k(1));
        assert_eq!(trie.values[0][1], k(2));
        // prefix counts
        assert_eq!(trie.counts[0][0], 3);
        assert_eq!(trie.counts[0][1], 1);
        // leaf multiplicity for (1,10) is 2
        assert_eq!(trie.counts[1][0], 2);
        assert_eq!(trie.counts[1][1], 1);
        assert_eq!(trie.counts[1][2], 1);
        assert_eq!(trie.total_rows(), 4);
    }

    #[test]
    fn seek_never_skips_intersection_keys() {
        let rows = (0..100).step_by(3).map(|i| vec![k(i)]).collect();
        let trie = Trie::build(vec![0], rows);
        let mut cur = TrieCursor::new(&trie);
        cur.open_root();

        for target in [0i64, 1, 2, 3, 97, 98, 99] {
            cur.idx = 0; // re-scan from beginning
            cur.seek(&k(target));
            // first multiple of 3 >= target
            let want = target + ((3 - target % 3) % 3);
            if want <= 99 {
                assert_eq!(cur.key(), &k(want), "seek({target})");
            } else {
                assert!(cur.at_end(), "seek({target}) should end");
            }
        }
        let ctr = cur.counters();
        assert!(ctr.seeks >= 7);
        assert!(
            ctr.seek_comparisons > 0,
            "binary search must count comparisons"
        );
    }

    #[test]
    fn null_sorts_before_values() {
        assert!(TrieKey(None) < k(0));
        assert_eq!(TrieKey(None), TrieKey(None));
    }

    #[test]
    fn navigation_up_and_rebind_round_trip() {
        let rows = vec![vec![k(1), k(10)], vec![k(1), k(11)], vec![k(2), k(20)]];
        let trie = Trie::build(vec![0, 1], rows);
        let mut cur = TrieCursor::new(&trie);
        cur.open_root();
        cur.seek(&k(1));
        cur.open_child();
        assert_eq!(cur.key(), &k(10));
        cur.next();
        assert_eq!(cur.key(), &k(11));
        cur.up();
        assert_eq!(cur.level(), 0);
        assert_eq!(cur.key(), &k(1));

        // Resume as if from a checkpoint deep in the tree.
        assert!(cur.rebind_path(&[k(2), k(20)]));
        assert_eq!(cur.level(), 1);
        assert_eq!(cur.key(), &k(20));
        assert!(!cur.rebind_path(&[k(2), k(21)]));
        assert!(!cur.rebind_path(&[k(9)]));
    }
}
