//! Ordered, materialized tries with leapfrog cursors.
//!
//! Each relation is stored as a prefix tree over a chosen *trie column order*
//! (join variables first, in global variable order, then private columns).
//! Repeated rows are collapsed: a leaf node carries the tuple's
//! [`Multiplicity`], so the join is evaluated as a multiset (bag) join rather
//! than a set join.
//!
//! The [`Cursor`] exposes the two moves Leapfrog Triejoin is built from:
//! [`Cursor::seek`] (binary-search leap, never a scan) and [`Cursor::next`],
//! plus `open`/`up` between trie levels.
use std::ops::Range;
use std::sync::Arc;

use serde::{Deserialize, Serialize};

use crate::domain::{Datum, Multiplicity};

/// One trie node. Children live contiguously in the next level's node vector
/// in [`Trie::levels`], addressed by `child_range`.
#[derive(Debug, Clone)]
struct Node {
    value: Datum,
    /// Bag count; nonzero only on leaf-level nodes.
    mult: Multiplicity,
    child_range: Range<usize>,
}

#[derive(Debug)]
pub struct Trie {
    /// Trie column order as indices into the originating relation schema.
    column_order: Vec<usize>,
    /// `levels[d]` holds the nodes at trie depth `d`.
    levels: Vec<Vec<Node>>,
    /// Distinct tuples in trie column order with their bag multiplicities.
    /// Kept for arity-1 (single relation) scans and for diagnostics.
    tuples: Vec<(Vec<Datum>, Multiplicity)>,
}

impl Trie {
    pub fn arity(&self) -> usize {
        self.column_order.len()
    }

    pub fn column_order(&self) -> &[usize] {
        &self.column_order
    }

    /// Distinct `(tuple, multiplicity)` pairs in trie column order.
    pub fn tuples(&self) -> &[(Vec<Datum>, Multiplicity)] {
        &self.tuples
    }

    /// Build from already-projected rows in **trie column order**, with
    /// duplicates collapsed into leaf multiplicities. Sorting + run-length
    /// grouping gives the ordered trie directly.
    pub fn from_ordered_rows(column_order: Vec<usize>, mut rows: Vec<Vec<Datum>>) -> Self {
        assert!(!column_order.is_empty(), "trie requires >= 1 column");
        let arity = column_order.len();
        rows.sort();

        // Deduplicate full tuples, preserving multiplicity.
        let mut tuples: Vec<(Vec<Datum>, Multiplicity)> = Vec::with_capacity(rows.len());
        for row in rows {
            if let Some(last) = tuples.last_mut() {
                if last.0 == row {
                    last.1 = last.1.saturating_add(1);
                    continue;
                }
            }
            tuples.push((row, 1));
        }

        let mut levels: Vec<Vec<Node>> = vec![Vec::new(); arity];
        build_level(&tuples, 0, &mut levels);
        Trie {
            column_order,
            levels,
            tuples,
        }
    }

    pub fn root_range(&self) -> Range<usize> {
        0..self.levels[0].len()
    }

    pub fn root_len(&self) -> usize {
        self.levels[0].len()
    }

    pub fn is_empty(&self) -> bool {
        self.levels[0].is_empty()
    }

    pub(crate) fn node_value(&self, depth: usize, idx: usize) -> &Datum {
        &self.levels[depth][idx].value
    }

    pub(crate) fn node_mult(&self, depth: usize, idx: usize) -> Multiplicity {
        self.levels[depth][idx].mult
    }

    pub(crate) fn child_range(&self, depth: usize, idx: usize) -> Range<usize> {
        self.levels[depth][idx].child_range.clone()
    }

    /// Collect `(private-value path, leaf multiplicity)` completions beneath
    /// the node at `(depth, idx)`. Returns one empty-path entry carrying the
    /// node's own multiplicity when the node is itself a leaf.
    pub fn collect_leaves(
        &self,
        depth: usize,
        idx: usize,
        path: &mut Vec<Datum>,
        out: &mut Vec<(Vec<Datum>, Multiplicity)>,
    ) {
        if depth + 1 == self.arity() {
            out.push((path.clone(), self.node_mult(depth, idx)));
            return;
        }
        for child in self.child_range(depth, idx) {
            path.push(self.node_value(depth + 1, child).clone());
            self.collect_leaves(depth + 1, child, path, out);
            path.pop();
        }
    }
}

/// Recursively append nodes for one prefix level. Children for a node are
/// appended contiguously, so each parent records its child index range.
fn build_level(tuples: &[(Vec<Datum>, Multiplicity)], depth: usize, levels: &mut [Vec<Node>]) {
    let arity = levels.len();
    let mut i = 0;
    while i < tuples.len() {
        let value = tuples[i].0[depth].clone();
        let start = i;
        while i < tuples.len() && tuples[i].0[depth] == value {
            i += 1;
        }
        let group = &tuples[start..i];
        let mut child_range = 0..0;
        let mult;
        if depth + 1 == arity {
            // All tuples in a leaf group are identical: sum their counts.
            mult = group.iter().map(|(_, m)| *m).sum();
        } else {
            let child_start = levels[depth + 1].len();
            build_level(group, depth + 1, levels);
            let child_end = levels[depth + 1].len();
            child_range = child_start..child_end;
            // Subtree multiplicity: number of bag tuples below this prefix.
            // Lets the engine read a relation's multiplicity at ANY prefix
            // depth, which is what prefix projection (dropping trailing
            // private columns) needs.
            mult = levels[depth + 1][child_start..child_end]
                .iter()
                .map(|n| n.mult)
                .sum();
        }
        levels[depth].push(Node {
            value,
            mult,
            child_range,
        });
    }
}

/// Access counters for a single join evaluation. Seek comparisons are counted
/// separately from seek *operations*: one leapfrog seek is one operation even
/// though it performs log(N) key comparisons internally.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Counters {
    pub seek_calls: u64,
    pub seek_key_comparisons: u64,
    pub next_calls: u64,
    pub opens: u64,
    pub ups: u64,
    pub value_probes: u64,
    pub emitted_assignments: u64,
    pub emitted_multiplicity: u128,
}

/// A positional cursor over one [`Trie`], bound for the lifetime of one join.
#[derive(Debug)]
pub struct Cursor<'a> {
    trie: &'a Trie,
    /// For every opened depth, the node range at that level and current pos.
    stack: Vec<Frame>,
}

#[derive(Debug, Clone)]
struct Frame {
    range: Range<usize>,
    pos: usize,
}

impl<'a> Cursor<'a> {
    pub fn new(trie: &'a Trie) -> Self {
        Self {
            trie,
            stack: Vec::with_capacity(trie.arity()),
        }
    }

    pub fn trie(&self) -> &'a Trie {
        self.trie
    }

    pub fn depth(&self) -> usize {
        self.stack.len().saturating_sub(1)
    }

    /// Node index of the cursor within its current level's vector.
    pub fn current_index(&self) -> usize {
        self.frame().pos
    }

    /// Number of siblings reachable at the current level (current to end).
    pub fn remaining(&self) -> usize {
        let f = self.frame();
        f.range.end.saturating_sub(f.pos)
    }

    pub fn is_open(&self) -> bool {
        !self.stack.is_empty()
    }

    /// Position at the first root node.
    pub fn open_root(&mut self, counters: &mut Counters) {
        let range = self.trie.root_range();
        self.stack.push(Frame {
            pos: range.start,
            range,
        });
        counters.opens += 1;
    }

    /// Open children of the current node. Caller guarantees current node is
    /// not a leaf (the engine only opens relations that have this variable).
    pub fn open(&mut self, counters: &mut Counters) {
        let range = self.node().child_range.clone();
        self.stack.push(Frame {
            pos: range.start,
            range,
        });
        counters.opens += 1;
    }

    pub fn up(&mut self, counters: &mut Counters) {
        self.stack.pop();
        counters.ups += 1;
    }

    pub fn at_end(&self) -> bool {
        self.stack
            .last()
            .map(|f| f.pos >= f.range.end)
            .unwrap_or(true)
    }

    fn frame(&self) -> &Frame {
        self.stack.last().expect("cursor accessed before open")
    }

    fn frame_mut(&mut self) -> &mut Frame {
        self.stack.last_mut().expect("cursor accessed before open")
    }

    fn node(&self) -> &Node {
        let f = self.frame();
        &self.trie.levels[self.stack.len() - 1][f.pos]
    }

    pub fn at(&self, counters: &mut Counters) -> &Datum {
        counters.value_probes += 1;
        &self.node().value
    }

    /// Leaf multiplicity of the node at the cursor's current depth. Only
    /// meaningful when the cursor is positioned on this relation's last
    /// variable (which holds for every relation at the deepest join level).
    pub fn current_mult(&self) -> Multiplicity {
        self.node().mult
    }

    pub fn next(&mut self, counters: &mut Counters) {
        counters.next_calls += 1;
        let f = self.frame_mut();
        f.pos += 1;
    }

    /// Leap to the first key `>= target` within the current level. This is a
    /// binary search over the contiguous, sorted sibling range — it never
    /// walks intermediate keys.
    pub fn seek(&mut self, target: &Datum, counters: &mut Counters) {
        counters.seek_calls += 1;
        let depth = self.stack.len() - 1;
        let level = &self.trie.levels[depth];
        let f = self.frame_mut();
        let base = f.range.start;
        let slice = &level[f.range.clone()];
        // partition_point: count of elements with value < target.
        let mut lo = 0usize;
        let mut hi = slice.len();
        while lo < hi {
            let mid = lo + (hi - lo) / 2;
            counters.seek_key_comparisons += 1;
            if slice[mid].value < *target {
                lo = mid + 1;
            } else {
                hi = mid;
            }
        }
        f.pos = base + lo;
    }
}

/// Shared `Arc<Trie>` handle stored in the catalog.
pub type SharedTrie = Arc<Trie>;
