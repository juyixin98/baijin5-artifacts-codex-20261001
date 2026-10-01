//! Resources & state: tables, typed batches, indexes and VERSIONED DELETES.
//!
//! Row identity is the stable append position (row id). Deleting a row does not
//! rewrite the index — it sets a bit in the versioned delete mask and bumps the
//! table version. Query evaluation always intersects results with the alive set
//! (`full universe − deletes`), so:
//! * a deleted row is excluded from TRUE/FALSE/UNKNOWN alike;
//! * two combined predicates must see the SAME alive set, or the combination
//!   is rejected as an incompatible index mode.

use std::collections::HashMap;
use std::sync::{Arc, RwLock};

use serde::Serialize;

use crate::batch::{typed_batch_from_json_rows, ColumnSpec, TypedBatch};
use crate::bits::Bitmap;
use crate::error::{Error, ErrorKind, Result};
use crate::index::TableIndexes;

/// Snapshot metadata of a table at one logical version.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct TableVersion {
    /// Monotonic version; bumped by every append and every delete commit.
    pub version: u64,
    pub total_rows: usize,
    pub alive_rows: usize,
    pub deleted_rows: usize,
}

/// A single table resource.
#[derive(Debug)]
pub struct Table {
    pub name: String,
    pub spec: Vec<ColumnSpec>,
    batch: TypedBatch,
    indexes: TableIndexes,
    /// Deleted rows over the full append universe.
    deleted: Bitmap,
    version: u64,
}

impl Table {
    fn new(name: String, spec: Vec<ColumnSpec>, batch: TypedBatch) -> Self {
        let columns = batch
            .columns
            .iter()
            .map(|(s, d)| (s.name.clone(), d.clone()))
            .collect();
        let indexes = TableIndexes::from_columns(columns);
        let deleted = Bitmap::zeros(batch.row_count());
        Self {
            name,
            spec,
            batch,
            indexes,
            deleted,
            version: 1,
        }
    }

    pub fn row_count(&self) -> usize {
        self.batch.row_count()
    }

    pub fn version_info(&self) -> TableVersion {
        let total = self.deleted.len();
        let deleted_rows = self.deleted.count_ones();
        TableVersion {
            version: self.version,
            total_rows: total,
            alive_rows: total - deleted_rows,
            deleted_rows,
        }
    }

    /// Current alive universe: full universe minus versioned deletes.
    /// Tail padding is zero by [`Bitmap`] construction.
    pub fn alive(&self) -> Bitmap {
        let full = Bitmap::ones(self.deleted.len());
        full.and_not(&self.deleted).expect("same universe")
    }

    pub fn deleted_bitmap(&self) -> &Bitmap {
        &self.deleted
    }

    pub fn indexes(&self) -> &TableIndexes {
        &self.indexes
    }

    pub fn batch(&self) -> &TypedBatch {
        &self.batch
    }

    /// Append rows; schema must match. Reindexes and bumps the version.
    pub fn append(
        &mut self,
        rows: &[serde_json::Map<String, serde_json::Value>],
    ) -> Result<TableVersion> {
        let more = typed_batch_from_json_rows(&self.spec, rows)?;
        // Extend the delete mask to the new universe length first, so the old
        // bits are preserved and new rows start alive.
        let added = more.row_count();
        let old_len = self.deleted.len();
        self.batch.append(more)?;
        let new_len = old_len + added;
        let mut grown = Bitmap::zeros(new_len);
        for i in self.deleted.set_indices() {
            grown.set(i, true);
        }
        self.deleted = grown;
        self.rebuild_indexes();
        self.version += 1;
        Ok(self.version_info())
    }

    /// Delete rows by stable row id. Returns the version after the commit.
    /// Deleting an already-deleted row is idempotent; deleting an out-of-range
    /// or an alive-but-nonexistent id is an error.
    pub fn delete_rows(&mut self, ids: &[usize]) -> Result<TableVersion> {
        let total = self.deleted.len();
        for id in ids {
            if *id >= total {
                return Err(Error::new(
                    ErrorKind::InvalidInput,
                    format!("row id {id} out of range 0..{total}"),
                ));
            }
        }
        let mut changed = false;
        for id in ids {
            if !self.deleted.get(*id) {
                self.deleted.set(*id, true);
                changed = true;
            }
        }
        if changed {
            self.version += 1;
        }
        Ok(self.version_info())
    }

    /// Restore a previously deleted row (test/admin helper); bumps version.
    pub fn restore_rows(&mut self, ids: &[usize]) -> Result<TableVersion> {
        let total = self.deleted.len();
        for id in ids {
            if *id >= total {
                return Err(Error::new(
                    ErrorKind::InvalidInput,
                    format!("row id {id} out of range 0..{total}"),
                ));
            }
            self.deleted.set(*id, false);
        }
        self.version += 1;
        Ok(self.version_info())
    }

    fn rebuild_indexes(&mut self) {
        let columns = self
            .batch
            .columns
            .iter()
            .map(|(s, d)| (s.name.clone(), d.clone()))
            .collect();
        self.indexes = TableIndexes::from_columns(columns);
    }
}

/// In-memory table registry (the "resource & state" layer).
#[derive(Default)]
pub struct Catalog {
    tables: HashMap<String, Arc<RwLock<Table>>>,
}

impl Catalog {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn create_table(
        &mut self,
        name: &str,
        spec: Vec<ColumnSpec>,
        rows: Vec<serde_json::Map<String, serde_json::Value>>,
    ) -> Result<Arc<RwLock<Table>>> {
        if self.tables.contains_key(name) {
            return Err(Error::new(
                ErrorKind::InvalidInput,
                format!("table '{name}' already exists"),
            ));
        }
        let batch = typed_batch_from_json_rows(&spec, &rows)?;
        let table = Table::new(name.to_string(), spec, batch);
        let arc = Arc::new(RwLock::new(table));
        self.tables.insert(name.to_string(), arc.clone());
        Ok(arc)
    }

    pub fn table(&self, name: &str) -> Result<Arc<RwLock<Table>>> {
        self.tables
            .get(name)
            .cloned()
            .ok_or_else(|| Error::new(ErrorKind::NotFound, format!("no table '{name}'")))
    }

    pub fn table_names(&self) -> Vec<String> {
        let mut names: Vec<String> = self.tables.keys().cloned().collect();
        names.sort();
        names
    }
}
