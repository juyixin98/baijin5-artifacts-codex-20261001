//! Versioned row-deletion tracking.
//!
//! Rows are addressed by a stable logical id (`0..n`). Each row carries a
//! current content version counter. Deleting a row stamps `(row, version)`
//! into the [`VersionMap`]. Two clocks are kept deliberately separate:
//!
//! * **content generation** (`index_version`): bumped only when row contents
//!   are rewritten. Bitmap indexes are mutually combinable only at the same
//!   content generation ([`VersionMap::ensure_compatible`]).
//! * **read head** (`head_version`): the latest known point in time, advanced
//!   by delete tombstones. A delete therefore excludes rows from the visible
//!   universe *without* invalidating combined indexes.
//!
//! A row is live at read version `v` when no tombstone `<= v` exists and its
//! content version is `<= v`. The live set is a zeroed-tail bitmap over the
//! full document universe so 3VL predicates can intersect it while preserving
//! the partition invariants.

use std::collections::HashMap;

use super::bitmap::Bitmap;
use crate::error::{Result, TviError};

/// A monotonically increasing generation counter.
pub type Version = u64;

/// Delete stamps keyed by logical row id: `deleted_at[row] = Some(version)`
/// means the row was removed at that version.
#[derive(Clone, Debug, Default)]
pub struct VersionMap {
    len: usize,
    /// Current content version of each row. Rewriting a row bumps this.
    row_version: Vec<Version>,
    /// Version at which the row was deleted, if ever.
    deleted_at: Vec<Option<Version>>,
    /// Content generation the current indexes were built at.
    index_version: Version,
    /// Newest point in time known to the table (max of content and deletes).
    head_version: Version,
    /// Free-form provenance, echoed into logs and API responses.
    provenance: HashMap<String, String>,
}

impl VersionMap {
    /// Fresh map: `len` live rows, clocks at 1.
    pub fn new(len: usize) -> Self {
        VersionMap {
            len,
            row_version: vec![1; len],
            deleted_at: vec![None; len],
            index_version: 1,
            head_version: 1,
            provenance: HashMap::new(),
        }
    }

    /// Number of logical rows (deleted rows still occupy an id).
    pub fn len(&self) -> usize {
        self.len
    }

    /// Whether the table has zero logical rows.
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    /// Content generation of the current indexes.
    pub fn index_version(&self) -> Version {
        self.index_version
    }

    /// Latest readable point in time (includes delete tombstones).
    pub fn head_version(&self) -> Version {
        self.head_version
    }

    /// Attach provenance (e.g. fixture name, build run id).
    pub fn with_meta(mut self, key: impl Into<String>, val: impl Into<String>) -> Self {
        self.provenance.insert(key.into(), val.into());
        self
    }

    /// Provenance snapshot.
    pub fn meta(&self) -> &HashMap<String, String> {
        &self.provenance
    }

    /// Delete row `row` at time `at_version` (must be >= 1). The content
    /// generation does not move: existing bitmap indexes stay combinable.
    pub fn delete(&mut self, row: usize, at_version: Version) -> Result<()> {
        if row >= self.len {
            return Err(TviError::InvalidQuery(format!(
                "cannot delete row {row}: universe has {} rows",
                self.len
            )));
        }
        if at_version == 0 {
            return Err(TviError::InvalidQuery(
                "delete versions start at 1; got 0".into(),
            ));
        }
        self.deleted_at[row] = Some(at_version);
        self.head_version = self.head_version.max(at_version);
        Ok(())
    }

    /// Re-write / upsert a row at a new content generation: it becomes live
    /// again and the index generation advances. All indexes must be rebuilt to
    /// the new generation before they may be combined
    /// ([`Self::ensure_compatible`]).
    pub fn bump_row(&mut self, row: usize, new_version: Version) -> Result<()> {
        if row >= self.len {
            return Err(TviError::InvalidQuery(format!(
                "cannot bump row {row}: universe has {} rows",
                self.len
            )));
        }
        if new_version <= self.row_version[row] {
            return Err(TviError::InvalidQuery(format!(
                "row {row} is already at version {}; cannot move backwards to {new_version}",
                self.row_version[row]
            )));
        }
        self.row_version[row] = new_version;
        self.deleted_at[row] = None;
        self.index_version = self.index_version.max(new_version);
        self.head_version = self.head_version.max(new_version);
        Ok(())
    }

    /// True when the row is visible at `as_of`: no tombstone `<= as_of`, and
    /// its content version does not exceed `as_of`.
    pub fn is_live_at(&self, row: usize, as_of: Version) -> bool {
        match self.deleted_at[row] {
            Some(d) if d <= as_of => false,
            _ => self.row_version[row] <= as_of,
        }
    }

    /// Bitmap over the full universe with 1 for rows live at `as_of`.
    /// Tail padding is zero by construction.
    pub fn live_bitmap_at(&self, as_of: Version) -> Bitmap {
        Bitmap::from_indices(
            self.len,
            (0..self.len).filter(move |&r| self.is_live_at(r, as_of)),
        )
        .expect("all indices in range")
    }

    /// Current live bitmap (at the read head).
    pub fn live_bitmap(&self) -> Bitmap {
        self.live_bitmap_at(self.head_version)
    }

    /// Combined-index compatibility check: bitmap indexes may only be combined
    /// when built over the same universe length **and** content generation.
    /// Delete tombstones live in the version map, not the indexes, and do not
    /// affect this comparison.
    pub fn ensure_compatible(&self, other_len: usize, other_version: Version) -> Result<()> {
        if other_len != self.len {
            return Err(TviError::UniverseMismatch {
                expected: self.len,
                found: other_len,
            });
        }
        if other_version != self.index_version {
            return Err(TviError::InvalidQuery(format!(
                "combined indexes disagree on content version: table at v{}, operand at v{other_version}",
                self.index_version
            )));
        }
        Ok(())
    }

    /// Versions of one row, for structured test logs.
    pub fn row_debug(&self, row: usize) -> String {
        format!(
            "row {row}: content_version={} deleted_at={:?}",
            self.row_version[row], self.deleted_at[row]
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn delete_excludes_from_universe_and_as_of_visibility() {
        let mut vm = VersionMap::new(5);
        vm.delete(2, 2).unwrap();
        assert_eq!(vm.head_version(), 2);
        assert_eq!(vm.index_version(), 1); // deletes don't bump content
        let live = vm.live_bitmap();
        assert_eq!(live.to_bit_string(), "11011");
        // Deletion at v2 is not visible to an as-of-1 snapshot.
        assert!(vm.is_live_at(2, 1));
        assert!(!vm.is_live_at(2, 2));
    }

    #[test]
    fn deletes_keep_indexes_compatible() {
        let mut vm = VersionMap::new(4);
        vm.delete(0, 2).unwrap();
        // Content generation unchanged: a v1 index still combines.
        assert!(vm.ensure_compatible(4, 1).is_ok());
        vm.bump_row(1, 3).unwrap();
        let err = vm.ensure_compatible(4, 1).unwrap_err();
        assert!(matches!(err, TviError::InvalidQuery(_)));
        assert!(vm.ensure_compatible(3, 3).is_err()); // length mismatch
    }

    #[test]
    fn rejects_bad_delete_and_version_rewind() {
        let mut vm = VersionMap::new(2);
        assert!(vm.delete(0, 0).is_err());
        assert!(vm.delete(9, 1).is_err());
        vm.bump_row(0, 3).unwrap();
        assert!(vm.bump_row(0, 2).is_err());
    }
}
