//! Resources and shared state: fixture manifest, typed table, version map and
//! the set of per-column bitmap indexes.
//!
//! Everything is loaded once at startup behind `Arc` and served read-only;
//! Axum handlers take [`AppState`] by clone. Load failures abort startup with a
//! typed error rather than degrading to an empty table.

use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;

use serde::Deserialize;

use crate::config::Config;
use crate::error::{Result, TviError};
use crate::index::batch::{ColumnMeta, LogicalType, TypedTable};
use crate::index::value_index::ColumnIndex;
use crate::index::{Version, VersionMap};

/// One declared column in the manifest.
#[derive(Clone, Debug, Deserialize)]
pub struct ColumnSpec {
    pub name: String,
    #[serde(rename = "type")]
    pub ty: String,
}

/// One versioned delete tombstone.
#[derive(Clone, Debug, Deserialize)]
pub struct DeleteSpec {
    pub row: usize,
    pub version: Version,
}

/// Fixture manifest (`fixtures/manifest.toml`).
#[derive(Clone, Debug, Deserialize)]
pub struct Manifest {
    pub table: String,
    /// CSV file name inside the fixture directory.
    pub csv: String,
    /// Logical content generation of the fixture; indexes build at it.
    #[serde(default = "one")]
    pub content_version: Version,
    pub columns: Vec<ColumnSpec>,
    #[serde(default)]
    pub deletes: Vec<DeleteSpec>,
}

fn one() -> Version {
    1
}

/// All loaded, cross-checked resources for one table.
#[derive(Debug)]
pub struct TableStore {
    pub manifest: Manifest,
    pub table: TypedTable,
    pub versions: VersionMap,
    indexes: HashMap<String, ColumnIndex>,
}

impl TableStore {
    /// Load CSV + manifest from the configured fixture directory and build all
    /// indexes. The construction path asserts every combined-index invariant
    /// up front.
    pub fn load(cfg: &Config) -> Result<Arc<TableStore>> {
        let manifest_path = &cfg.data.manifest;
        let manifest_text = std::fs::read_to_string(manifest_path).map_err(|e| {
            TviError::Io(format!("reading manifest {}: {e}", manifest_path.display()))
        })?;
        let manifest: Manifest = toml::from_str(&manifest_text).map_err(|e| {
            TviError::Io(format!("parsing manifest {}: {e}", manifest_path.display()))
        })?;
        Self::from_manifest(manifest, &cfg.data.fixture_dir)
    }

    /// Build a store from an already-parsed manifest (used by tests too).
    pub fn from_manifest(manifest: Manifest, fixture_dir: &Path) -> Result<Arc<TableStore>> {
        if manifest.columns.is_empty() {
            return Err(TviError::Io("manifest declares no columns".into()));
        }
        let mut names = Vec::new();
        for c in &manifest.columns {
            if names.iter().any(|n: &String| n == &c.name) {
                return Err(TviError::Io(format!("duplicate column `{}`", c.name)));
            }
            names.push(c.name.clone());
        }

        let columns: Vec<ColumnMeta> = manifest
            .columns
            .iter()
            .map(|c| {
                Ok(ColumnMeta {
                    name: c.name.clone(),
                    logical: LogicalType::parse(&c.ty)?,
                })
            })
            .collect::<Result<_>>()?;

        let csv_path = fixture_dir.join(&manifest.csv);
        let table = TypedTable::from_csv_path(manifest.table.clone(), columns, &csv_path)?;

        // Version map starts at the manifest's content generation; the clock
        // never moves backwards.
        let mut versions =
            VersionMap::new(table.len()).with_meta("fixture", csv_path.display().to_string());
        if manifest.content_version != 1 {
            // Advance every row's content version together (fresh fixture load).
            for row in 0..table.len() {
                versions.bump_row(row, manifest.content_version)?;
            }
        }
        for d in &manifest.deletes {
            versions.delete(d.row, d.version)?;
        }

        // Build every index at the content generation.
        let gen = versions.index_version();
        let mut indexes = HashMap::new();
        for (i, meta) in table.columns.iter().enumerate() {
            let idx = ColumnIndex::build(&table, i, gen)?;
            // Combined-index compatibility is enforced for every index.
            versions.ensure_compatible(idx.len(), idx.version())?;
            indexes.insert(meta.name.clone(), idx);
        }

        Ok(Arc::new(TableStore {
            manifest,
            table,
            versions,
            indexes,
        }))
    }

    /// Read access to a column index.
    pub fn index(&self, column: &str) -> Option<&ColumnIndex> {
        self.indexes.get(column)
    }

    /// All index names.
    pub fn index_names(&self) -> Vec<String> {
        let mut v: Vec<String> = self.indexes.keys().cloned().collect();
        v.sort();
        v
    }

    /// Index map for query binding.
    pub fn indexes(&self) -> &HashMap<String, ColumnIndex> {
        &self.indexes
    }
}

/// Axum shared state.
#[derive(Clone)]
pub struct AppState {
    pub store: Arc<TableStore>,
    pub config: Arc<Config>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn manifest() -> Manifest {
        Manifest {
            table: "people".into(),
            csv: "people.csv".into(),
            content_version: 1,
            columns: vec![
                ColumnSpec {
                    name: "id".into(),
                    ty: "int".into(),
                },
                ColumnSpec {
                    name: "age".into(),
                    ty: "int".into(),
                },
                ColumnSpec {
                    name: "name".into(),
                    ty: "text".into(),
                },
                ColumnSpec {
                    name: "active".into(),
                    ty: "bool".into(),
                },
                ColumnSpec {
                    name: "note".into(),
                    ty: "text".into(),
                },
            ],
            deletes: vec![DeleteSpec { row: 6, version: 2 }],
        }
    }

    #[test]
    fn loads_fixture_and_applies_versioned_deletes() {
        let dir = PathBuf::from("fixtures");
        let store = TableStore::from_manifest(manifest(), &dir).unwrap();
        assert_eq!(store.table.len(), 7);
        assert_eq!(store.versions.index_version(), 1);
        assert_eq!(store.versions.head_version(), 2);
        assert!(!store.versions.is_live_at(6, 2));
        assert!(store.versions.is_live_at(6, 1));
    }

    #[test]
    fn rejects_duplicate_columns() {
        let mut m = manifest();
        m.columns.push(ColumnSpec {
            name: "id".into(),
            ty: "int".into(),
        });
        assert!(TableStore::from_manifest(m, Path::new("fixtures")).is_err());
    }
}
