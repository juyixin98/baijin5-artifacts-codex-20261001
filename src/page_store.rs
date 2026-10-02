//! Page-object store: fixed-size content objects on the filesystem plus a
//! persisted reference-count table.
//!
//! Layout under `<data_dir>`:
//!
//! ```text
//! pages/<obj_id>.page   raw page bytes (exactly `page_size` bytes)
//! meta/store.json       { page_size, next_id, objects: {obj_id: refcount} }
//! ```
//!
//! Persistence contract: individual mutations (`create`, `add_ref`,
//! `release`, `overwrite`) update the in-memory table and the object files,
//! but `store.json` is only written when the caller invokes [`PageStore::persist`]
//! at a commit boundary. The engine persists after every committed operation,
//! so a crash can leave at most orphaned object files (file without a table
//! entry). Orphans are reported by [`PageStore::scan_orphans`] at open/audit
//! time and are never silently deleted.
//!
//! Refcount contract: every object in the table has `refcount >= 1`. Any
//! violation discovered on this layer (unknown id, underflow, missing object
//! file) is reported as an [`ErrorCategory::Integrity`] error; the engine
//! responds by quarantining the service.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::error::{Result, ServiceError};

pub type ObjId = u64;

#[derive(Serialize, Deserialize)]
struct StoreMeta {
    page_size: usize,
    next_id: ObjId,
    objects: BTreeMap<ObjId, u32>,
}

pub struct PageStore {
    pages_dir: PathBuf,
    meta_path: PathBuf,
    page_size: usize,
    capacity: usize,
    objects: BTreeMap<ObjId, u32>,
    next_id: ObjId,
}

impl PageStore {
    /// Open an existing store, or create an empty one if `meta/store.json`
    /// does not exist yet. `capacity == 0` in the config is rejected earlier.
    pub fn open(data_dir: &Path, page_size: usize, capacity: usize) -> Result<Self> {
        let pages_dir = data_dir.join("pages");
        let meta_path = data_dir.join("meta").join("store.json");
        fs::create_dir_all(&pages_dir)
            .and_then(|_| fs::create_dir_all(data_dir.join("meta")))
            .map_err(|e| ServiceError::internal("io", format!("create store dirs: {e}")))?;

        if meta_path.exists() {
            let raw = fs::read_to_string(&meta_path)
                .map_err(|e| ServiceError::internal("io", format!("read store.json: {e}")))?;
            let meta: StoreMeta = serde_json::from_str(&raw).map_err(|e| {
                ServiceError::integrity("meta_corrupt", format!("parse store.json: {e}"))
            })?;
            if meta.page_size != page_size {
                return Err(ServiceError::conflict(
                    "config_mismatch",
                    format!(
                        "store page_size {} != configured {}",
                        meta.page_size, page_size
                    ),
                ));
            }
            Ok(Self {
                pages_dir,
                meta_path,
                page_size,
                capacity,
                objects: meta.objects,
                next_id: meta.next_id,
            })
        } else {
            let store = Self {
                pages_dir,
                meta_path,
                page_size,
                capacity,
                objects: BTreeMap::new(),
                next_id: 0,
            };
            store.persist()?;
            Ok(store)
        }
    }

    pub fn page_size(&self) -> usize {
        self.page_size
    }
    pub fn capacity(&self) -> usize {
        self.capacity
    }
    /// Number of live page objects (shared + exclusive).
    pub fn len(&self) -> usize {
        self.objects.len()
    }
    pub fn is_empty(&self) -> bool {
        self.objects.is_empty()
    }
    /// Sum of all refcounts. The engine checks this against the number of
    /// references held by live + snapshot mappings after every mutation.
    pub fn total_refs(&self) -> u64 {
        self.objects.values().map(|&rc| rc as u64).sum()
    }
    pub fn refcount(&self, id: ObjId) -> Option<u32> {
        self.objects.get(&id).copied()
    }
    pub fn contains(&self, id: ObjId) -> bool {
        self.objects.contains_key(&id)
    }

    fn object_path(&self, id: ObjId) -> PathBuf {
        self.pages_dir.join(format!("{id}.page"))
    }

    /// Allocate a new object with refcount 1. Fails with
    /// `ResourceExhausted/capacity_exhausted` when the store is full.
    pub fn create(&mut self, content: &[u8]) -> Result<ObjId> {
        if content.len() != self.page_size {
            return Err(ServiceError::internal(
                "bad_object_size",
                format!(
                    "object content {} bytes, page_size {}",
                    content.len(),
                    self.page_size
                ),
            ));
        }
        if self.objects.len() >= self.capacity {
            return Err(ServiceError::exhausted(
                "capacity_exhausted",
                format!(
                    "page object capacity exhausted: used={} capacity={} (need 1 more)",
                    self.objects.len(),
                    self.capacity
                ),
            ));
        }
        let id = self.next_id;
        self.next_id += 1;
        write_file_atomic(&self.object_path(id), content)?;
        self.objects.insert(id, 1);
        Ok(id)
    }

    /// Initialization helper: create an object with an explicit refcount.
    /// Used exactly once to seed the shared zero page.
    pub fn create_with_refcount(&mut self, content: &[u8], refcount: u32) -> Result<ObjId> {
        let id = self.create(content)?;
        *self.objects.get_mut(&id).expect("just created") = refcount;
        Ok(id)
    }

    pub fn add_ref(&mut self, id: ObjId) -> Result<()> {
        match self.objects.get_mut(&id) {
            None => Err(ServiceError::integrity(
                "refcount_anomaly",
                format!("add_ref on unknown object {id}"),
            )),
            Some(rc) => {
                *rc = rc.checked_add(1).ok_or_else(|| {
                    ServiceError::integrity(
                        "refcount_anomaly",
                        format!("refcount overflow on object {id}"),
                    )
                })?;
                Ok(())
            }
        }
    }

    /// Decrement a refcount; frees the object (file + table entry) at zero.
    /// Returns `true` when the object was freed.
    pub fn release(&mut self, id: ObjId) -> Result<bool> {
        match self.objects.get_mut(&id) {
            None => Err(ServiceError::integrity(
                "refcount_anomaly",
                format!("release on unknown object {id}"),
            )),
            Some(rc) => {
                if *rc == 0 {
                    return Err(ServiceError::integrity(
                        "refcount_anomaly",
                        format!("release would underflow object {id} (refcount already 0)"),
                    ));
                }
                *rc -= 1;
                if *rc == 0 {
                    self.objects.remove(&id);
                    let path = self.object_path(id);
                    if path.exists() {
                        fs::remove_file(&path).map_err(|e| {
                            ServiceError::internal(
                                "io",
                                format!("remove object file {}: {e}", path.display()),
                            )
                        })?;
                    }
                    Ok(true)
                } else {
                    Ok(false)
                }
            }
        }
    }

    pub fn read(&self, id: ObjId) -> Result<Vec<u8>> {
        if !self.objects.contains_key(&id) {
            return Err(ServiceError::integrity(
                "refcount_anomaly",
                format!("read of unknown object {id}"),
            ));
        }
        let path = self.object_path(id);
        let bytes = fs::read(&path).map_err(|e| {
            ServiceError::integrity(
                "object_file_missing",
                format!(
                    "object {id} in table but file {} unreadable: {e}",
                    path.display()
                ),
            )
        })?;
        if bytes.len() != self.page_size {
            return Err(ServiceError::integrity(
                "object_size_mismatch",
                format!(
                    "object {id} file is {} bytes, expected {}",
                    bytes.len(),
                    self.page_size
                ),
            ));
        }
        Ok(bytes)
    }

    /// Overwrite an object in place. Only legal for exclusive objects
    /// (refcount == 1); the engine enforces that, the store double-checks.
    pub fn overwrite(&self, id: ObjId, content: &[u8]) -> Result<()> {
        match self.objects.get(&id) {
            None => Err(ServiceError::integrity(
                "refcount_anomaly",
                format!("overwrite of unknown object {id}"),
            )),
            Some(&rc) if rc != 1 => Err(ServiceError::integrity(
                "refcount_anomaly",
                format!("in-place overwrite of shared object {id} (refcount {rc})"),
            )),
            Some(_) => {
                if content.len() != self.page_size {
                    return Err(ServiceError::internal(
                        "bad_object_size",
                        format!(
                            "overwrite content {} bytes, page_size {}",
                            content.len(),
                            self.page_size
                        ),
                    ));
                }
                write_file_atomic(&self.object_path(id), content)
            }
        }
    }

    /// Object files present on disk but absent from the refcount table.
    pub fn scan_orphans(&self) -> Result<Vec<ObjId>> {
        let mut orphans = Vec::new();
        let entries = fs::read_dir(&self.pages_dir)
            .map_err(|e| ServiceError::internal("io", format!("scan pages dir: {e}")))?;
        for entry in entries {
            let entry =
                entry.map_err(|e| ServiceError::internal("io", format!("read dir: {e}")))?;
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if let Some(stem) = name.strip_suffix(".page") {
                if let Ok(id) = stem.parse::<ObjId>() {
                    if !self.objects.contains_key(&id) {
                        orphans.push(id);
                    }
                }
            }
        }
        orphans.sort_unstable();
        Ok(orphans)
    }

    /// Objects in the table whose file is missing on disk.
    pub fn scan_missing_files(&self) -> Vec<ObjId> {
        self.objects
            .keys()
            .filter(|id| !self.object_path(**id).exists())
            .copied()
            .collect()
    }

    /// All `(obj_id, refcount)` pairs — used by the engine's audit.
    pub fn snapshot_objects(&self) -> Vec<(ObjId, u32)> {
        self.objects.iter().map(|(&k, &v)| (k, v)).collect()
    }

    /// Test hook: directly set a refcount, simulating metadata corruption.
    #[doc(hidden)]
    pub fn debug_set_refcount(&mut self, obj: ObjId, rc: u32) -> Result<()> {
        match self.objects.get_mut(&obj) {
            Some(slot) => {
                *slot = rc;
                self.persist()
            }
            None => Err(ServiceError::input(
                "unknown_object",
                format!("debug_set_refcount: object {obj} not in table"),
            )),
        }
    }

    /// Write the refcount table to disk (tmp file + rename).
    pub fn persist(&self) -> Result<()> {
        let meta = StoreMeta {
            page_size: self.page_size,
            next_id: self.next_id,
            objects: self.objects.clone(),
        };
        let raw = serde_json::to_string_pretty(&meta)
            .map_err(|e| ServiceError::internal("serialize", format!("store meta: {e}")))?;
        write_file_atomic(&self.meta_path, raw.as_bytes())
    }
}

/// Write `content` to `path` via a temporary sibling file + rename, so a
/// crash never leaves a half-written file at `path`.
fn write_file_atomic(path: &Path, content: &[u8]) -> Result<()> {
    let tmp = path.with_extension("tmp");
    fs::write(&tmp, content)
        .map_err(|e| ServiceError::internal("io", format!("write {}: {e}", tmp.display())))?;
    fs::rename(&tmp, path).map_err(|e| {
        ServiceError::internal(
            "io",
            format!("rename {} -> {}: {e}", tmp.display(), path.display()),
        )
    })
}
