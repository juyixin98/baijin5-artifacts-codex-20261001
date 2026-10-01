//! Accumulated result set with the two distinct dedup mechanisms kept apart.
//!
//! - **Set dedup** (`UNION DISTINCT`) is global: a row identity already
//!   accumulated never enters the working table again. The identity is the
//!   business-column tuple plus the cycle marker — it deliberately *never*
//!   contains the rendered path string, which changes on every traversal and
//!   would make the set grow without bound.
//! - **Path dedup** is per traversal: a key repeating among a row's ancestors
//!   marks that row as a cycle. It lives in the expansion logic
//!   ([`crate::executor`]) and reads only the declared key columns.

use std::collections::HashSet;

use crate::batch::Value;

use super::working::WorkingRow;

/// Identity used by global set deduplication.
///
/// Tuple of `(business values, cycle flag)`. The path column is excluded by
/// construction — callers build this from [`WorkingRow`] fields directly.
pub type RowIdentity = (Vec<Value>, bool);

/// Alias used by executors that hold identities before the row exists.
pub type AccumulatedIdentity = RowIdentity;

/// Accumulated output rows plus the set-dedup membership structure.
#[derive(Debug, Default)]
pub struct Accumulated {
    /// Output rows in emission order (business values + path + cycle flag).
    output: Vec<Vec<Value>>,
    /// Membership for `UNION DISTINCT`. Unused (but maintained) under
    /// `UNION ALL`, where every produced row survives.
    seen: HashSet<RowIdentity>,
}

impl Accumulated {
    /// Empty accumulator.
    pub fn new() -> Self {
        Self::default()
    }

    /// Identity of a working row for set-dedup purposes.
    pub fn identity(row: &WorkingRow) -> RowIdentity {
        (row.business.clone(), row.cycle)
    }

    /// Whether `row` was already accumulated under set semantics.
    pub fn contains(&self, row: &WorkingRow) -> bool {
        self.seen.contains(&Self::identity(row))
    }

    /// Membership test by a precomputed identity.
    pub fn contains_identity(&self, identity: &RowIdentity) -> bool {
        self.seen.contains(identity)
    }

    /// Number of output rows accumulated so far.
    pub fn len(&self) -> usize {
        self.output.len()
    }

    /// Whether no rows have accumulated.
    pub fn is_empty(&self) -> bool {
        self.output.is_empty()
    }

    /// Accumulate one row.
    ///
    /// Returns `false` (and changes nothing) when `distinct` is set and the
    /// row identity was already present. Cycle rows and ordinary rows share
    /// the same business tuple but have different identities, so a node first
    /// reached normally and later reached as a cycle is still reported once
    /// each way.
    pub fn insert(&mut self, row: &WorkingRow, distinct: bool) -> bool {
        if distinct && !self.seen.insert(Self::identity(row)) {
            return false;
        }
        if !distinct {
            // Keep the membership structure honest even in ALL mode; it is not
            // consulted for rejection, but aids diagnostics and tests.
            self.seen.insert(Self::identity(row));
        }
        self.output.push(row.output_row(""));
        true
    }

    /// All accumulated output rows (business columns + path + cycle flag).
    pub fn output_rows(&self) -> &[Vec<Value>] {
        &self.output
    }

    /// Number of distinct identities observed (diagnostics).
    pub fn distinct_count(&self) -> usize {
        self.seen.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::batch::Value;

    #[test]
    fn distinct_rejects_repeat_business_row() {
        let mut acc = Accumulated::new();
        let a = WorkingRow {
            business: vec![Value::Int64(1)],
            path: vec![vec![Value::Int64(1)]],
            cycle: false,
            depth: 0,
        };
        let mut b = a.clone();
        b.path.push(vec![Value::Int64(2)]);
        // Different path, same business tuple -> still a duplicate.
        assert!(acc.insert(&a, true));
        assert!(!acc.insert(&b, true));
        assert_eq!(acc.len(), 1);
    }

    #[test]
    fn cycle_arrival_has_its_own_identity() {
        let mut acc = Accumulated::new();
        let normal = WorkingRow {
            business: vec![Value::Int64(1)],
            path: vec![vec![Value::Int64(1)]],
            cycle: false,
            depth: 0,
        };
        let cyclic = WorkingRow {
            business: vec![Value::Int64(1)],
            path: vec![vec![Value::Int64(1)], vec![Value::Int64(1)]],
            cycle: true,
            depth: 1,
        };
        assert!(acc.insert(&normal, true));
        assert!(acc.insert(&cyclic, true));
        assert_eq!(acc.len(), 2);
    }

    #[test]
    fn all_mode_keeps_every_copy() {
        let mut acc = Accumulated::new();
        let a = WorkingRow {
            business: vec![Value::Int64(7)],
            path: vec![vec![Value::Int64(7)]],
            cycle: false,
            depth: 0,
        };
        assert!(acc.insert(&a, false));
        assert!(acc.insert(&a, false));
        assert_eq!(acc.len(), 2);
        assert_eq!(acc.distinct_count(), 1);
    }
}
