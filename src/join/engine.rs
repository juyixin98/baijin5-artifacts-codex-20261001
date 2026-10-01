//! Generalized multi-relation Leapfrog Triejoin engine.
//!
//! The three-table triangle query is the arity-3 case of this same algorithm;
//! nothing here is special-cased to three relations.
//!
//! Invariants relied upon (established by [`crate::query::compile`]):
//! * each relation trie's leading columns are the join variables it owns, in
//!   global join-variable order, followed by private columns;
//! * `plan.owners[d]` lists every relation containing join variable `d`;
//! * the join graph is connected, so every solved variable intersects rather
//!   than multiplies independent relation sets.
//!
//! At each variable the active iterators leapfrog via binary-search
//! [`crate::trie::Cursor::seek`]: no pairwise intermediate result and no
//! Cartesian product is ever materialized.
use std::cmp::Ordering;
use std::collections::BTreeMap;

use crate::domain::{Datum, Multiplicity};
use crate::join::cursor;
use crate::query::Plan;
use crate::trie::{Counters, Cursor, SharedTrie};

/// One delivered output row with its bag multiplicity.
#[derive(Debug, Clone)]
pub struct EmittedRow {
    /// Values in SELECTED attribute order.
    pub values: Vec<Datum>,
    pub multiplicity: u128,
}

#[derive(Debug)]
pub struct ExecOutput {
    pub rows: Vec<EmittedRow>,
    pub counters: Counters,
    /// Token for the immediately following row; `None` when exhausted.
    pub next_cursor: Option<String>,
    pub truncated: bool,
}

struct Engine<'a> {
    plan: &'a Plan,
    tries: Vec<&'a SharedTrie>,
    cursors: Vec<Cursor<'a>>,
    counters: Counters,
    /// Current value of each global join variable.
    assign: Vec<Option<Datum>>,
    rows: Vec<EmittedRow>,
    /// Full projected-key -> aggregate bag multiplicity. Projection
    /// deduplicates keys that are non-adjacent in canonical order, so the
    /// complete answer must be accumulated before a page can be sliced. The
    /// join itself still builds no intermediate product: only the necessary
    /// projected output is aggregated.
    agg: BTreeMap<Vec<Datum>, u128>,
    limit: u64,
    /// Exclusive lower bound in SELECTED-attribute order (decoded cursor).
    after: Option<Vec<Datum>>,
    /// Seek pruning is valid only when selection equals canonical order.
    seek_prune: bool,
    truncated: bool,
}

impl<'a> Engine<'a> {
    fn new(plan: &'a Plan, limit: u64, after: Option<Vec<Datum>>) -> Self {
        let tries: Vec<&SharedTrie> = plan.relations.iter().map(|r| &r.trie).collect();
        let cursors = tries.iter().map(|t| Cursor::new(t)).collect();
        Self {
            plan,
            tries,
            cursors,
            counters: Counters::default(),
            assign: vec![None; plan.join_vars.len()],
            rows: Vec::new(),
            agg: BTreeMap::new(),
            limit,
            after,
            seek_prune: !plan.is_projection(),
            truncated: false,
        }
    }

    /// Solve global join variable `var`. Returns `true` to stop (page full).
    fn solve(&mut self, var: usize) -> bool {
        let active: Vec<usize> = self.plan.owners[var].clone();

        for &rel in &active {
            if !self.cursors[rel].is_open() {
                self.cursors[rel].open_root(&mut self.counters);
            } else {
                self.cursors[rel].open(&mut self.counters);
            }
        }

        // Resume pruning: the bound seek applies only while the already-solved
        // prefix equals the cursor tuple. At the first greater value the bound
        // is satisfied transitively for all deeper variables.
        if self.seek_prune {
            if let Some(after) = &self.after {
                let prefix_matches = self.assign[..var]
                    .iter()
                    .enumerate()
                    .all(|(d, v)| v.as_ref() == Some(&after[d]));
                if prefix_matches {
                    let bound = after[var].clone();
                    for &rel in &active {
                        self.cursors[rel].seek(&bound, &mut self.counters);
                    }
                }
            }
        }

        let stop = self.leapfrog(var, &active);

        for &rel in &active {
            self.cursors[rel].up(&mut self.counters);
        }
        stop
    }

    /// Leapfrog intersection over the active iterators at one variable.
    ///
    /// Canonical step: each round, seek the iterator positioned at the
    /// **minimum** key up to the current **maximum** key. A binary-search leap
    /// skips everything in between; when min catches max all iterators agree.
    fn leapfrog(&mut self, var: usize, active: &[usize]) -> bool {
        loop {
            if self.limit_reached() {
                return true;
            }
            if active.iter().any(|&rel| self.cursors[rel].at_end()) {
                return false;
            }

            // Locate minimum- and maximum-key iterators by direct comparison.
            let mut min_rel = active[0];
            let mut max_rel = active[0];
            for &rel in &active[1..] {
                let key = self.cursors[rel].at(&mut self.counters);
                if key < self.cursors[min_rel].at(&mut self.counters) {
                    min_rel = rel;
                }
                if key > self.cursors[max_rel].at(&mut self.counters) {
                    max_rel = rel;
                }
            }

            let max_key = self.cursors[max_rel].at(&mut self.counters).clone();
            let min_key = self.cursors[min_rel].at(&mut self.counters).clone();
            if min_key == max_key {
                // Every active iterator sits on the same key: intersection.
                self.assign[var] = Some(max_key);
                let stop = if var + 1 == self.plan.join_vars.len() {
                    self.emit_assignment()
                } else {
                    self.solve(var + 1)
                };
                if stop {
                    return true;
                }
                // Consume the aligned key on every active iterator.
                for &rel in active {
                    self.cursors[rel].next(&mut self.counters);
                }
            } else {
                // Leap the lagging (minimum-key) iterator to the maximum.
                self.cursors[min_rel].seek(&max_key, &mut self.counters);
            }
        }
    }

    /// Enumerate private-column completions for the current join assignment.
    /// Join multiplicities across relations multiply; each canonical tuple is
    /// handed to [`Engine::deliver`] in canonical lexicographic order.
    fn emit_assignment(&mut self) -> bool {
        let join_values: Vec<Datum> = self.assign.iter().map(|v| v.clone().unwrap()).collect();

        let mut per_relation: Vec<Vec<(Vec<Datum>, Multiplicity)>> = Vec::new();
        for (rel_idx, prep) in self.plan.relations.iter().enumerate() {
            let depth = self.cursors[rel_idx].depth();
            let idx = self.cursors[rel_idx].current_index();
            let mut leaves = Vec::new();
            let mut path = Vec::new();
            self.tries[rel_idx].collect_leaves(depth, idx, &mut path, &mut leaves);
            debug_assert!(
                !leaves.is_empty(),
                "relation '{}' aligned but has no completion",
                prep.name
            );
            per_relation.push(leaves);
        }

        // Mixed-radix enumeration, rightmost relation fastest => canonical
        // (relation-declaration) lexicographic order.
        let mut choice = vec![0usize; per_relation.len()];
        loop {
            let mut tuple = join_values.clone();
            let mut mult: u128 = 1;
            for (ri, leaves) in per_relation.iter().enumerate() {
                let (path, leaf_mult) = &leaves[choice[ri]];
                tuple.extend_from_slice(path);
                mult = mult.saturating_mul(u128::from(*leaf_mult));
            }
            if self.deliver(tuple, mult) {
                return true;
            }

            let mut k = choice.len();
            let mut rolled = false;
            while k > 0 {
                k -= 1;
                choice[k] += 1;
                if choice[k] < per_relation[k].len() {
                    for c in &mut choice[k + 1..] {
                        *c = 0;
                    }
                    rolled = true;
                    break;
                }
            }
            if !rolled {
                return false;
            }
        }
    }

    /// Accept one canonical tuple. In projection mode aggregate its bag
    /// multiplicity across the (possibly non-adjacent) full traversal and
    /// never stop early; in canonical-order mode apply the resume bound and
    /// the page limit directly. Returns `true` when traversal must stop.
    fn deliver(&mut self, canonical: Vec<Datum>, mult: u128) -> bool {
        let projected: Vec<Datum> = self
            .plan
            .select
            .iter()
            .map(|attr| {
                canonical[self
                    .plan
                    .output_attributes
                    .iter()
                    .position(|a| a == attr)
                    .expect("select validated")]
                .clone()
            })
            .collect();

        self.counters.emitted_assignments += 1;
        self.counters.emitted_multiplicity =
            self.counters.emitted_multiplicity.saturating_add(mult);

        if self.plan.is_projection() {
            // Keys may repeat non-adjacently (a projected join variable can
            // recur under different private completions), so accumulate into
            // the global multiset; pages are sliced after traversal.
            let entry = self.agg.entry(projected).or_insert(0u128);
            *entry = entry.saturating_add(mult);
            false
        } else {
            if let Some(after) = &self.after {
                if projected.as_slice().cmp(after.as_slice()) != Ordering::Greater {
                    return false; // at or before the resume point
                }
            }
            self.rows.push(EmittedRow {
                values: projected,
                multiplicity: mult,
            });
            if self.rows.len() as u64 >= self.limit {
                self.truncated = true;
                return true;
            }
            false
        }
    }

    fn run(mut self) -> ExecOutput {
        if self.plan.is_projection() {
            // Traverse the full join, aggregate projected multiplicities, then
            // slice one page. The join still builds no intermediate product;
            // only the necessary projected output is held.
            if self.plan.join_vars.is_empty() {
                self.scan_single_relation();
            } else {
                self.solve(0);
            }
            self.materialize_projection_page();
        } else if self.plan.join_vars.is_empty() {
            self.scan_single_relation();
        } else if self.solve(0) {
            self.truncated = true;
        }

        let next_cursor = if self.truncated {
            self.rows.last().map(|r| cursor::encode_after(&r.values))
        } else {
            None
        };
        ExecOutput {
            rows: self.rows,
            counters: self.counters,
            next_cursor,
            truncated: self.truncated,
        }
    }

    fn limit_reached(&self) -> bool {
        self.rows.len() as u64 >= self.limit
    }

    /// Sort aggregated projected keys, apply the resume bound, and slice one
    /// page without splitting an aggregated group.
    fn materialize_projection_page(&mut self) {
        let after = self.after.clone();
        let mut ordered: Vec<EmittedRow> = self
            .agg
            .iter()
            .map(|(k, m)| EmittedRow {
                values: k.clone(),
                multiplicity: *m,
            })
            .collect();
        if let Some(after) = &after {
            ordered.retain(|r| r.values.as_slice().cmp(after.as_slice()) == Ordering::Greater);
        }
        let total = ordered.len() as u64;
        ordered.truncate(self.limit as usize);
        self.truncated = total > self.limit;
        self.rows = ordered;
    }

    /// Arity-1 case: one relation, no shared attribute. Tuples are already
    /// distinct, sorted and carry bag multiplicity. Projection + bound are
    /// applied through the same aggregation path as joins.
    fn scan_single_relation(&mut self) {
        let tuples: Vec<(Vec<Datum>, Multiplicity)> = self.tries[0].tuples().to_vec();
        for (tuple, mult) in tuples {
            if self.deliver(tuple, u128::from(mult)) {
                self.truncated = true;
                return;
            }
        }
    }
}

/// Execute a validated plan. `after` is a decoded cursor in selected order.
pub fn execute(plan: &Plan, limit: u64, after: Option<Vec<Datum>>) -> ExecOutput {
    Engine::new(plan, limit, after).run()
}
