//! Manifest checkpoint: the persistent state of the service.
//!
//! The manifest is the single source of truth across restarts. It is
//! rewritten atomically (tmp file + rename) after every successful
//! mutation. Page files themselves are the data plane; the manifest is
//! the metadata plane (refcounts, snapshot tables, counters).
//!
//! Tradeoff: a crash between a page-file write and the manifest rename
//! can leave an orphan page file (never referenced). `diag::verify`
//! reports refcount inconsistencies; orphan files are reclaimed by
//! deleting the data directory — acceptable for a local fixture service
//! and documented in the README.

use std::{
    collections::BTreeMap,
    fs,
    io::ErrorKind,
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};

use crate::{
    engine::{SnapId, SnapshotMeta},
    error::AppError,
    store::{Counters, PhysId},
};

pub const MANIFEST_VERSION: u32 = 1;

#[derive(Debug, Serialize, Deserialize)]
pub struct Manifest {
    pub version: u32,
    pub page_size: usize,
    pub logical_pages: usize,
    pub capacity_pages: usize,
    pub next_snap: SnapId,
    pub next_phys: PhysId,
    pub refcounts: BTreeMap<PhysId, u32>,
    pub snapshots: BTreeMap<SnapId, SnapshotMeta>,
    pub counters: Counters,
}

/// Atomically replace the manifest at `path`.
pub fn save(path: &Path, manifest: &Manifest) -> Result<(), AppError> {
    let tmp: PathBuf = path.with_extension("json.tmp");
    let bytes = serde_json::to_vec_pretty(manifest)
        .map_err(|e| AppError::Compute(format!("manifest serialization failed: {e}")))?;
    fs::write(&tmp, bytes)?;
    fs::rename(&tmp, path)?;
    Ok(())
}

/// Load the manifest, or `None` if none exists yet.
pub fn load(path: &Path) -> Result<Option<Manifest>, AppError> {
    match fs::read(path) {
        Ok(bytes) => {
            let manifest: Manifest = serde_json::from_slice(&bytes).map_err(|e| {
                AppError::Compute(format!("manifest deserialization failed: {e}"))
            })?;
            if manifest.version != MANIFEST_VERSION {
                return Err(AppError::Compute(format!(
                    "unsupported manifest version {} (expected {MANIFEST_VERSION})",
                    manifest.version
                )));
            }
            Ok(Some(manifest))
        }
        Err(e) if e.kind() == ErrorKind::NotFound => Ok(None),
        Err(e) => Err(e.into()),
    }
}
