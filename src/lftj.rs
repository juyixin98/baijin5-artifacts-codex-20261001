//! Leapfrog Triejoin query operator.
//!
//! The algorithm is the worst-case-optimal Leapfrog Triejoin: at each global
//! variable `v`, every atom that binds `v` contributes one trie iterator
//! positioned under the prefix already fixed, and the *intersection* of their
//! sibling sets is found by the leapfrog seek protocol (rotate over iterators
//! sorted by remaining size, binary-search each one to the running maximum).
//! No pairwise intermediate relation is ever built —
//! [`EngineStats::intermediate_tuples_materialized`] is therefore always
//! zero, and tests assert that.
//!
//! References: Veldhuizen, "Leapfrog Triejoin: A Worst-case Optimal Join
//! Algorithm" (2012/2014).

use std::cmp::Ordering;

use crate::plan::Plan;
use crate::trie::{AccessCounters, Trie, TrieCursor, TrieKey};
use crate::value::Cell;

/// Per-run counters and shape information.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EngineStats {
    pub atoms: usize,
    pub arity: usize,
    /// Output rows produced, counting multiset multiplicity.
    pub emitted_rows: u64,
    /// Tuples visited by the scan but skipped to honour a resume frontier.
    pub skipped_rows: u64,
    /// Always zero: leapfrog intersects in place, no intermediate products.
    pub intermediate_tuples_materialized: u64,
    /// Summed trie access counters across all atoms.
    pub seeks: u64,
    pub seek_comparisons: u64,
    pub nexts: u64,
    pub child_opens: u64,
    /// Why the scan stopped.
    pub stop_reason: StopReason,
}

/// Why a scan ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StopReason {
    /// Exhausted the whole join.
    Complete,
    /// Output row cap reached; resumable via the returned frontier.
    OutputLimit,
    /// Access budget exhausted before completeness could be established;
    /// resumable when a frontier exists, otherwise restart from the beginning.
    BudgetExhausted,
}

impl StopReason {
    /// Stable snake-case identifier used on the wire and in diagnostics.
    pub fn as_str(self) -> &'static str {
        match self {
            StopReason::Complete => "complete",
            StopReason::OutputLimit => "output_limit",
            StopReason::BudgetExhausted => "budget_exhausted",
        }
    }
}

/// Resume frontier: the last fully delivered global assignment plus how many
/// multiset copies of it had already been delivered across previous runs.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResumePoint {
    pub path: Vec<TrieKey>,
    pub copies_delivered: u64,
}

/// Result of one engine run.
#[derive(Debug, Clone)]
pub struct RunOutput {
    pub rows: Vec<Vec<Cell>>,
    pub stats: EngineStats,
    pub next: Option<ResumePoint>,
}

/// One atom bound to a built trie.
struct AtomTrie {
    /// global variable index -> trie level, for variables this atom binds.
    level_for_var: Vec<Option<usize>>,
    trie: Trie,
}

/// The join engine for one validated [`Plan`].
pub struct JoinEngine {
    plan: Plan,
    atom_tries: Vec<AtomTrie>,
    /// global variable index -> atoms binding it.
    atoms_for_var: Vec<Vec<usize>>,
}

impl JoinEngine {
    /// Build ordered tries for every atom, restricting the global variable
    /// order to each atom's attributes.
    pub fn build(plan: Plan) -> Self {
        let n = plan.arity();
        let mut atom_tries = Vec::with_capacity(plan.atoms.len());

        for atom in &plan.atoms {
            let mut level_for_var = vec![None; n];
            for (level, (global, _)) in atom.bindings.iter().enumerate() {
                level_for_var[*global] = Some(level);
            }

            let rows = plan.rows_for_relation(atom.relation_index);
            let projected: Vec<Vec<TrieKey>> = rows
                .iter()
                .map(|row| {
                    atom.bindings
                        .iter()
                        .map(|(_, col_idx)| match &row[*col_idx] {
                            Cell::Value(v) => TrieKey(Some(v.clone())),
                            Cell::Null => TrieKey(None),
                        })
                        .collect()
                })
                .collect();

            let permutation: Vec<usize> =
                atom.bindings.iter().map(|(_, col_idx)| *col_idx).collect();
            atom_tries.push(AtomTrie {
                level_for_var,
                trie: Trie::build(permutation, projected),
            });
        }

        let mut atoms_for_var = vec![Vec::new(); n];
        for (a, at) in atom_tries.iter().enumerate() {
            for (v, bound) in at.level_for_var.iter().enumerate() {
                if bound.is_some() {
                    atoms_for_var[v].push(a);
                }
            }
        }

        JoinEngine {
            plan,
            atom_tries,
            atoms_for_var,
        }
    }

    pub fn arity(&self) -> usize {
        self.plan.arity()
    }

    /// The validated plan this engine executes.
    pub fn plan(&self) -> &Plan {
        &self.plan
    }

    /// Run the join.
    ///
    /// - `limit` caps materialised output rows;
    /// - `resume` skips to an interruption frontier;
    /// - `access_budget` (if set) bounds total trie accesses, stopping before
    ///   completeness is provable ([`StopReason::BudgetExhausted`]).
    pub fn run(
        &self,
        limit: Option<u64>,
        resume: Option<ResumePoint>,
        access_budget: Option<u64>,
    ) -> RunOutput {
        let n = self.plan.arity();
        let (frontier_path, resume_copies) = match &resume {
            Some(rp) => (Some(rp.path.clone()), rp.copies_delivered),
            None => (None, 0),
        };
        let mut ctx = RunCtx {
            engine: self,
            cursors: self
                .atom_tries
                .iter()
                .map(|at| TrieCursor::new(&at.trie))
                .collect(),
            assignment: vec![TrieKey(None); n],
            rows: Vec::new(),
            limit,
            emitted: 0,
            skipped: 0,
            budget: access_budget,
            stop: StopReason::Complete,
            resume_path: resume.as_ref().map(|r| r.path.clone()),
            resume_copies,
            skipping: resume.is_some(),
            frontier_path,
            frontier_copies: resume_copies,
        };
        ctx.search(0);

        let stats = ctx.snapshot();
        let rows = ctx.rows;
        let copies = ctx.frontier_copies;

        // The resumption frontier is the last fully delivered assignment with
        // the cumulative count of its delivered copies.
        let next = if ctx.stop == StopReason::Complete {
            None
        } else {
            ctx.frontier_path.map(|path| ResumePoint {
                path,
                copies_delivered: copies,
            })
        };

        RunOutput { stats, rows, next }
    }
}

/// Mutable per-run execution state.
struct RunCtx<'a> {
    engine: &'a JoinEngine,
    cursors: Vec<TrieCursor<'a>>,
    assignment: Vec<TrieKey>,
    rows: Vec<Vec<Cell>>,
    limit: Option<u64>,
    emitted: u64,
    skipped: u64,
    budget: Option<u64>,
    stop: StopReason,
    /// Resume input.
    resume_path: Option<Vec<TrieKey>>,
    resume_copies: u64,
    skipping: bool,
    /// Last delivered assignment + cumulative copies of it.
    frontier_path: Option<Vec<TrieKey>>,
    frontier_copies: u64,
}

impl<'a> RunCtx<'a> {
    /// Output-column index -> current cell, following user-facing order.
    fn materialise_row(&self) -> Vec<Cell> {
        self.engine
            .plan
            .output_columns
            .iter()
            .map(|(name, _)| {
                let g = self.engine.plan.global_index[name];
                match &self.assignment[g].0 {
                    Some(v) => Cell::Value(v.clone()),
                    None => Cell::Null,
                }
            })
            .collect()
    }

    fn aggregated(&self) -> AccessCounters {
        let mut acc = AccessCounters::default();
        for c in &self.cursors {
            let c = c.counters();
            acc.seeks += c.seeks;
            acc.seek_comparisons += c.seek_comparisons;
            acc.nexts += c.nexts;
            acc.child_opens += c.child_opens;
        }
        acc
    }

    fn over_budget(&self) -> bool {
        matches!(self.budget, Some(b) if self.aggregated().total() > b)
    }

    fn snapshot(&self) -> EngineStats {
        let acc = self.aggregated();
        EngineStats {
            atoms: self.engine.atom_tries.len(),
            arity: self.engine.arity(),
            emitted_rows: self.emitted,
            skipped_rows: self.skipped,
            intermediate_tuples_materialized: 0,
            seeks: acc.seeks,
            seek_comparisons: acc.seek_comparisons,
            nexts: acc.nexts,
            child_opens: acc.child_opens,
            stop_reason: self.stop,
        }
    }

    /// Core recursive search at global variable `v`. Returns false when the
    /// scan must unwind immediately (limit or budget).
    fn search(&mut self, v: usize) -> bool {
        let n = self.engine.arity();
        if v == n {
            return self.emit_leaf();
        }

        let active: Vec<usize> = self.engine.atoms_for_var[v].clone();
        debug_assert!(!active.is_empty(), "every variable belongs to an atom");

        // Each active atom's cursor is one trie level above the one for v:
        // the first variable it binds opens the root, every later one opens
        // a child under its current prefix. Atoms that skip v stay put.
        for &a in &active {
            if self.cursors[a].level() < 0 {
                self.cursors[a].open_root();
            } else {
                self.cursors[a].open_child();
            }
        }

        loop {
            if active.iter().any(|&a| self.cursors[a].at_end()) {
                break;
            }
            if self.over_budget() {
                self.stop = StopReason::BudgetExhausted;
                return false;
            }

            // Find the smallest current key and the largest current key.
            let mut min_at = active[0];
            let mut min_key = self.cursors[active[0]].key().clone();
            let mut max_key = min_key.clone();
            for &a in active.iter().skip(1) {
                let key = self.cursors[a].key().clone();
                if key < min_key {
                    min_key = key.clone();
                    min_at = a;
                }
                if key > max_key {
                    max_key = key;
                }
            }

            if min_key == max_key {
                // Every active iterator agrees: this is one intersection key.
                self.assignment[v] = max_key;
                if !self.search(v + 1) {
                    return false;
                }
                // Consume the common key in every sibling set.
                for &a in &active {
                    self.cursors[a].next();
                }
            } else {
                // Leap the lagging (minimum-key) iterator to the ceiling.
                self.cursors[min_at].seek(&max_key);
            }
        }

        // Close exactly the levels this frame opened.
        for &a in &active {
            if self.cursors[a].level() == 0 {
                self.cursors[a].reset();
            } else {
                self.cursors[a].up();
            }
        }
        true
    }

    /// Emit one complete global assignment, honouring multiplicity, resume
    /// skipping and the page limit.
    fn emit_leaf(&mut self) -> bool {
        let weight: u64 = self.cursors.iter().map(|c| c.count()).product();
        let row = self.materialise_row();

        for _ in 0..weight {
            if self.skipping && !self.past_resume_point() {
                self.skipped += 1;
                continue;
            }
            self.skipping = false;

            if let Some(lim) = self.limit {
                if self.emitted >= lim {
                    if self.stop == StopReason::Complete {
                        self.stop = StopReason::OutputLimit;
                    }
                    return false;
                }
            }

            self.rows.push(row.clone());
            self.emitted += 1;
            match &self.frontier_path {
                Some(path) if *path == self.assignment => self.frontier_copies += 1,
                _ => {
                    self.frontier_path = Some(self.assignment.clone());
                    self.frontier_copies = 1;
                }
            }
        }
        true
    }

    /// Whether the current assignment is strictly past the resume frontier.
    /// Equal assignments are "past" only after the carried copies are spent.
    fn past_resume_point(&mut self) -> bool {
        let Some(path) = &self.resume_path else {
            return true;
        };
        match self.assignment.cmp(path) {
            Ordering::Less => false,
            Ordering::Greater => true,
            Ordering::Equal => {
                if self.resume_copies > 0 {
                    self.resume_copies -= 1;
                    false
                } else {
                    true
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::plan::NullPolicy;
    use crate::schema::{Column, ColumnType, Relation};
    use crate::value::{Cell, Scalar};

    fn rel(name: &str, cols: &[&str], rows: Vec<Vec<i64>>) -> Relation {
        let mut r = Relation::new(
            name,
            cols.iter()
                .map(|c| Column::new(*c, ColumnType::Int))
                .collect(),
        );
        r.set_rows(
            rows.into_iter()
                .map(|row| row.into_iter().map(Cell::from).collect())
                .collect(),
        )
        .unwrap();
        r
    }

    fn ints(row: &[Cell]) -> Vec<i64> {
        row.iter()
            .map(|c| match c {
                Cell::Value(Scalar::Int(i)) => *i,
                _ => panic!("expected int"),
            })
            .collect()
    }

    #[test]
    fn two_table_natural_join_with_multiplicity() {
        // R(a,b): (1,10) twice,(2,20); S(b,c): (10,100),(20,200)
        let r = rel(
            "R",
            &["a", "b"],
            vec![vec![1, 10], vec![1, 10], vec![2, 20]],
        );
        let s = rel("S", &["b", "c"], vec![vec![10, 100], vec![20, 200]]);
        let plan = Plan::natural_join(vec![r, s], NullPolicy::Reject, 6).unwrap();
        let out = JoinEngine::build(plan).run(None, None, None);

        let mut got: Vec<Vec<i64>> = out.rows.iter().map(|r| ints(r)).collect();
        got.sort();
        assert_eq!(
            got,
            vec![vec![1, 10, 100], vec![1, 10, 100], vec![2, 20, 200]]
        );
        assert_eq!(out.stats.intermediate_tuples_materialized, 0);
        assert_eq!(out.stats.stop_reason, StopReason::Complete);
        assert!(out.next.is_none());
    }

    #[test]
    fn triangle_query_matches_known_answer() {
        // Complete graph K4 with oriented edges (i < j): exactly 4 triangles.
        let edge = |name: &str, pair: (&str, &str)| {
            let rows: Vec<Vec<i64>> = (0..4)
                .flat_map(|i| (i + 1..4).map(move |j| vec![i, j]))
                .collect();
            rel(name, &[pair.0, pair.1], rows)
        };
        let r = edge("R", ("a", "b"));
        let s = edge("S", ("b", "c"));
        let t = edge("T", ("a", "c"));
        let plan = Plan::natural_join(vec![r, s, t], NullPolicy::Reject, 6).unwrap();
        let out = JoinEngine::build(plan).run(None, None, None);

        let mut got: Vec<Vec<i64>> = out.rows.iter().map(|r| ints(r)).collect();
        got.sort();
        assert_eq!(
            got,
            vec![vec![0, 1, 2], vec![0, 1, 3], vec![0, 2, 3], vec![1, 2, 3],]
        );
        assert_eq!(out.stats.emitted_rows, 4);
        assert_eq!(out.stats.intermediate_tuples_materialized, 0);
    }

    #[test]
    fn free_attribute_duplicates_combine_by_product() {
        // (a=1,b=1) appears 2x in R; (b=1,c=9) appears 3x in S -> 6 copies.
        let r = rel("R", &["a", "b"], vec![vec![1, 1], vec![1, 1], vec![2, 1]]);
        let s = rel("S", &["b", "c"], vec![vec![1, 9], vec![1, 9], vec![1, 9]]);
        let plan = Plan::natural_join(vec![r, s], NullPolicy::Reject, 6).unwrap();
        let out = JoinEngine::build(plan).run(None, None, None);
        assert_eq!(out.stats.emitted_rows, 2 * 3 + 3);
        let n1 = out
            .rows
            .iter()
            .filter(|row| ints(row) == vec![1, 1, 9])
            .count();
        assert_eq!(n1, 6);
    }

    #[test]
    fn pagination_resumes_without_loss_or_duplication() {
        let r = rel(
            "R",
            &["a", "b"],
            vec![vec![1, 1], vec![2, 1], vec![3, 1], vec![4, 1]],
        );
        let s = rel("S", &["b", "c"], vec![vec![1, 9], vec![1, 9]]);
        let plan = Plan::natural_join(vec![r, s], NullPolicy::Reject, 6).unwrap();
        let engine = JoinEngine::build(plan);

        let mut paged = Vec::new();
        let mut resume: Option<ResumePoint> = None;
        for page in 0..10 {
            let out = engine.run(Some(3), resume.clone(), None);
            paged.extend(out.rows.iter().map(|r| ints(r)));
            match out.next {
                Some(rp) => {
                    assert_eq!(out.stats.stop_reason, StopReason::OutputLimit);
                    resume = Some(rp);
                }
                None => {
                    assert_eq!(out.stats.stop_reason, StopReason::Complete);
                    break;
                }
            }
            assert!(page < 9, "pagination did not terminate");
        }

        let full = engine.run(None, None, None);
        let mut expected: Vec<Vec<i64>> = full.rows.iter().map(|r| ints(r)).collect();
        expected.sort();
        paged.sort();
        assert_eq!(
            paged, expected,
            "paginated union must equal one-shot result"
        );
        assert_eq!(expected.len(), 8);
    }

    #[test]
    fn budget_stop_is_distinguished_and_resumable() {
        // A budget below the cost of reaching the first leaf cannot decide
        // anything and offers no frontier (client restarts from scratch).
        let r = rel("R", &["a", "b"], (0..40).map(|i| vec![i, i]).collect());
        let s = rel(
            "S",
            &["b", "c"],
            (0..40).map(|b| vec![b, b * 100]).collect(),
        );
        let plan = Plan::natural_join(vec![r, s], NullPolicy::Reject, 6).unwrap();
        let engine = JoinEngine::build(plan);

        let full = engine.run(None, None, None);
        assert_eq!(full.stats.emitted_rows, 40);
        let total_accesses = full.stats.seeks
            + full.stats.seek_comparisons
            + full.stats.nexts
            + full.stats.child_opens;

        // Tiny budget: cannot even reach the first leaf -> no frontier.
        let early = engine.run(None, None, Some(1));
        assert_eq!(early.stats.stop_reason, StopReason::BudgetExhausted);
        assert_eq!(early.stats.emitted_rows, 0);
        assert!(
            early.next.is_none(),
            "nothing emitted -> restart from start"
        );

        // Half-of-work budget: some rows emitted, completeness not reached.
        let half = (total_accesses / 2).max(2);
        let partial = engine.run(None, None, Some(half));
        assert_eq!(partial.stats.stop_reason, StopReason::BudgetExhausted);
        assert!(
            partial.stats.emitted_rows > 0,
            "half budget must emit some rows"
        );
        assert!(
            partial.stats.emitted_rows < 40,
            "half budget must not finish; budget={half}, total={total_accesses}"
        );
        assert!(partial.next.is_some(), "partial progress must be resumable");

        // Resuming without a budget yields the exact complement.
        let mut got: Vec<Vec<i64>> = partial.rows.iter().map(|r| ints(r)).collect();
        let rest = engine.run(None, partial.next.clone(), None);
        assert_eq!(rest.stats.stop_reason, StopReason::Complete);
        got.extend(rest.rows.iter().map(|r| ints(r)));
        got.sort();

        let mut expected: Vec<Vec<i64>> = full.rows.iter().map(|r| ints(r)).collect();
        expected.sort();
        assert_eq!(got, expected);
    }
}
