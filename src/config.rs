//! Static service configuration, validated once at startup.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use crate::error::AppError;

pub const DEFAULT_PAGE_SIZE: usize = 4096;
pub const DEFAULT_LOGICAL_PAGES: usize = 64;
pub const DEFAULT_CAPACITY_PAGES: usize = 1024;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    /// Bytes per page. Fixed for the lifetime of a data directory.
    pub page_size: usize,
    /// Logical pages addressable per snapshot (page indices 0..logical_pages).
    pub logical_pages: usize,
    /// Total physical pages the store may hold across all snapshots.
    pub capacity_pages: usize,
    /// Data directory holding `manifest.json` and `pages/`.
    #[serde(skip)]
    pub data_dir: PathBuf,
}

impl Config {
    pub fn new(data_dir: impl Into<PathBuf>) -> Self {
        Config {
            page_size: DEFAULT_PAGE_SIZE,
            logical_pages: DEFAULT_LOGICAL_PAGES,
            capacity_pages: DEFAULT_CAPACITY_PAGES,
            data_dir: data_dir.into(),
        }
    }

    pub fn validate(&self) -> Result<(), AppError> {
        if self.page_size == 0 {
            return Err(AppError::Input("page_size must be > 0".into()));
        }
        if self.logical_pages == 0 {
            return Err(AppError::Input("logical_pages must be > 0".into()));
        }
        if self.capacity_pages == 0 {
            return Err(AppError::Input("capacity_pages must be > 0".into()));
        }
        Ok(())
    }

    pub fn pages_dir(&self) -> PathBuf {
        self.data_dir.join("pages")
    }

    pub fn manifest_path(&self) -> PathBuf {
        self.data_dir.join("manifest.json")
    }
}
