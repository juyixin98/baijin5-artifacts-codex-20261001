//! Service configuration, loaded from TOML.

use crate::error::{FailureCategory, MergeError};
use serde::Deserialize;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Deserialize)]
pub struct Config {
    /// Address the diagnostic API binds to, e.g. "127.0.0.1:8487".
    pub listen: String,
    /// Isolation root: every layer path and output dir in a request must
    /// resolve to a location under this directory.
    pub workspace_root: PathBuf,
    /// JSONL file where run records are persisted.
    pub store_path: PathBuf,
}

impl Config {
    pub fn load(path: &Path) -> Result<Self, MergeError> {
        let raw = std::fs::read_to_string(path).map_err(|e| {
            MergeError::new(
                FailureCategory::Io,
                format!("read config {}: {e}", path.display()),
            )
        })?;
        let mut cfg: Config = toml::from_str(&raw).map_err(|e| {
            MergeError::new(
                FailureCategory::InvalidPath,
                format!("parse config {}: {e}", path.display()),
            )
        })?;
        // The workspace root must exist; canonicalize it once so containment
        // checks compare canonical-to-canonical.
        cfg.workspace_root = cfg.workspace_root.canonicalize().map_err(|e| {
            MergeError::new(
                FailureCategory::Io,
                format!("canonicalize workspace_root: {e}"),
            )
        })?;
        Ok(cfg)
    }
}
