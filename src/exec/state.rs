//! Mutable run state: the materialized result sink and the two *distinct*
//! deduplication mechanisms.
//!
//! * **Set dedup** (`UNION`): a global set of row fingerprints over the SQL
//!   `SET` columns (every column except the cycle marker). A row already in the
//!   result is neither re-emitted nor re-expanded.
//! * **Path dedup** (cycle marking): a per-branch membership test of the
//!   *declared key* against that branch's path list. It is orthogonal to set
//!   dedup and never looks at the whole row (the path grows every step, so a
//!   whole-row comparison could never repeat and cycles would go undetected).
//!
//! The state deliberately does NOT own the frontier: the working table holds
//! only the current round's new rows and is managed by the executor, exactly
//! as in semi-naïve evaluation — old rows stay in the result sink and are
//! never re-scanned.

use std::collections::hash_map::DefaultHasher;
use std::collections::HashSet;
use std::hash::Hasher;

use crate::batch::{Scalar, TypedBatch};
use crate::plan::CycleConfig;

/// One item waiting for expansion.
#[derive(Debug, Clone)]
pub struct FrontierEntry {
    /// Full-width row in view column order (managed columns included).
    pub row: Vec<Scalar>,
    /// Expansions from the seed: seeds are depth 0.
    pub depth: usize,
}

/// Result of classifying a candidate child row.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChildVerdict {
    /// New row: emit and expand.
    Fresh,
    /// Revisits a key already on this branch. Must be emitted once with the
    /// cycle marker set; the branch stops there.
    Cycle,
    /// Duplicate under `UNION` set semantics: suppressed entirely.
    Duplicate,
}

pub struct RunState {
    result: TypedBatch,
    seen: HashSet<u64>,
    /// SQL SET/search columns: every user column plus the cycle marker.
    set_columns: Vec<usize>,
    cycle: Option<CycleResolved>,
    dedup_sets: bool,
    pub duplicates_suppressed: usize,
    pub cycles_marked: usize,
}

struct CycleResolved {
    key_pos: usize,
    path_pos: usize,
    marker_pos: usize,
}

impl RunState {
    /// Initialize by sinking seed rows (already assembled in full view
    /// order) and seizing their fingerprints.
    ///
    /// For `UNION` (`dedup_sets = true`) duplicate seed rows are suppressed
    /// and counted, matching set semantics on the seed term.
    pub fn new(
        result_schema: Vec<crate::plan::ColumnDecl>,
        seed_rows: Vec<Vec<Scalar>>,
        cycle: Option<&CycleConfig>,
        key_pos: Option<usize>,
        path_pos: Option<usize>,
        marker_pos: Option<usize>,
        dedup_sets: bool,
    ) -> (Self, Vec<Vec<Scalar>>, usize) {
        let cycle = match (cycle, key_pos, path_pos, marker_pos) {
            (Some(_), Some(k), Some(p), Some(m)) => Some(CycleResolved {
                key_pos: k,
                path_pos: p,
                marker_pos: m,
            }),
            (None, _, _, _) => None,
            _ => unreachable!("cycle positions are resolved together"),
        };

        // SQL SET/search columns: every user column plus the cycle marker;
        // the PATH column is excluded (it changes every step, so including it
        // would disable set dedup on reachable nodes entirely).
        let path_pos = cycle.as_ref().map(|c| c.path_pos);
        let set_columns = (0..result_schema.len())
            .filter(|i| Some(*i) != path_pos)
            .collect::<Vec<_>>();

        let mut seen = HashSet::with_capacity(seed_rows.len());
        let mut result = TypedBatch::empty(result_schema);
        let mut initial_frontier = Vec::with_capacity(seed_rows.len());
        let mut duplicates_suppressed = 0usize;

        for row in seed_rows {
            let fp = TypedBatch::row_fingerprint(&row, &set_columns);
            if dedup_sets && !seen.insert(fp) {
                duplicates_suppressed += 1;
                continue;
            }
            seen.insert(fp);
            initial_frontier.push(row.clone());
            result.append_rows(std::iter::once(row));
        }

        let state = Self {
            result,
            seen,
            set_columns,
            cycle,
            dedup_sets,
            duplicates_suppressed,
            cycles_marked: 0,
        };
        (state, initial_frontier, duplicates_suppressed)
    }

    pub fn result(&self) -> &TypedBatch {
        &self.result
    }

    pub fn result_row_count(&self) -> usize {
        self.result.row_count()
    }

    /// Classify a freshly projected child row (cycle marker must be `false`).
    ///
    /// Path membership (cycle) is always checked, independently of the set
    /// quantifier. Global set dedup runs only for `UNION`; for `UNION ALL`
    /// `dedup_sets` is `false` and every non-cycle child is [`ChildVerdict::Fresh`].
    pub fn classify(&mut self, row: &[Scalar], dedup_sets: bool) -> ChildVerdict {
        if let Some(cyc) = &self.cycle {
            // The child's stored path ENDS at its own key (the path was just
            // extended), so a cycle means the key occurs among the ANCESTORS —
            // the path minus its final element. Checking the whole extended
            // path would flag every row as a cycle.
            if ancestor_path_contains_key(&row[cyc.path_pos], &row[cyc.key_pos]) {
                return ChildVerdict::Cycle;
            }
        }
        if dedup_sets {
            let fp = TypedBatch::row_fingerprint(row, &self.set_columns);
            if self.seen.contains(&fp) {
                return ChildVerdict::Duplicate;
            }
        }
        ChildVerdict::Fresh
    }

    /// Sink a fresh row into the result and seize its fingerprint. Does not
    /// enqueue anything: the caller owns the frontier and decides BFS vs DFS.
    pub fn register_fresh(&mut self, row: Vec<Scalar>) {
        let fp = TypedBatch::row_fingerprint(&row, &self.set_columns);
        self.seen.insert(fp);
        self.result.append_rows(std::iter::once(row));
    }

    /// Emit a cycle-marker row. Does not enqueue: the halting branch must not
    /// be expanded further.
    ///
    /// Returns `true` when the marker row was actually emitted. Under `UNION`
    /// a second marker with the same search columns is suppressed (the first
    /// visit marker for a key pair); under `UNION ALL` every marker is kept.
    pub fn admit_cycle(&mut self, mut row: Vec<Scalar>) -> bool {
        if let Some(cyc) = &self.cycle {
            row[cyc.marker_pos] = Scalar::Bool(true);
        }
        let fp = TypedBatch::row_fingerprint(&row, &self.set_columns);
        if self.dedup_sets && !self.seen.insert(fp) {
            self.duplicates_suppressed += 1;
            return false;
        }
        self.seen.insert(fp);
        self.result.append_rows(std::iter::once(row));
        self.cycles_marked += 1;
        true
    }

    /// Whether a cycle-marker candidate would actually be emitted under the
    /// current set policy. Used to make depth-bound probes exact.
    pub fn would_admit_cycle(&self, row: &[Scalar]) -> bool {
        if !self.dedup_sets {
            return true;
        }
        let mut probe = row.to_vec();
        if let Some(cyc) = &self.cycle {
            probe[cyc.marker_pos] = Scalar::Bool(true);
        }
        let fp = TypedBatch::row_fingerprint(&probe, &self.set_columns);
        !self.seen.contains(&fp)
    }
    pub fn note_duplicate(&mut self) {
        self.duplicates_suppressed += 1;
    }
}

/// Is `key` present among the ANCESTORS encoded in the child's path?
///
/// The child's stored path is `ancestors + [key]`, so membership is tested on
/// all but the final element. Types always match for validated plans; the
/// fallback is conservative (`false`).
fn ancestor_path_contains_key(path: &Scalar, key: &Scalar) -> bool {
    match (path, key) {
        (Scalar::IntList(xs), Scalar::Int(k)) => xs[..xs.len().saturating_sub(1)].contains(k),
        (Scalar::Utf8List(xs), Scalar::Utf8(k)) => {
            xs[..xs.len().saturating_sub(1)].iter().any(|x| x == k)
        }
        _ => false,
    }
}

/// Hash helper used by diagnostics and reference cross-checks.
pub fn fingerprint_row(row: &[Scalar], columns: &[usize]) -> u64 {
    let mut h = DefaultHasher::new();
    for &i in columns {
        row[i].fingerprint_component(&mut h);
    }
    h.finish()
}
