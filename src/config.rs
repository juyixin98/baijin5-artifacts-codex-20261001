//! Service configuration. All knobs are plain values so tests can construct
//! configurations directly; `from_env` is the production entry point.

use std::path::PathBuf;

use crate::error::{Result, ServiceError};

#[derive(Debug, Clone)]
pub struct ServiceConfig {
    pub data_dir: PathBuf,
    /// Bytes per page. Fixed for the lifetime of a data directory.
    pub page_size: usize,
    /// Number of pages in the logical volume. Fixed for the lifetime of a
    /// data directory.
    pub page_count: usize,
    /// Maximum number of live page *objects* (shared + exclusive). This is
    /// the resource the capacity-exhaustion contract is defined against.
    pub capacity: usize,
    pub bind: String,
    /// Maximum number of page writes accepted in a single atomic batch.
    pub max_batch_writes: usize,
}

impl ServiceConfig {
    pub fn from_env() -> Result<Self> {
        let data_dir = std::env::var("COW_DATA_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from("./data"));
        let page_size = env_usize("COW_PAGE_SIZE", 4096)?;
        let page_count = env_usize("COW_PAGE_COUNT", 256)?;
        let capacity = env_usize("COW_CAPACITY", 4096)?;
        let max_batch_writes = env_usize("COW_MAX_BATCH_WRITES", 1024)?;
        let bind = std::env::var("COW_BIND").unwrap_or_else(|_| "127.0.0.1:8080".to_string());
        let cfg = Self {
            data_dir,
            page_size,
            page_count,
            capacity,
            bind,
            max_batch_writes,
        };
        cfg.validate()?;
        Ok(cfg)
    }

    pub fn validate(&self) -> Result<()> {
        if self.page_size == 0 {
            return Err(ServiceError::input("bad_config", "page_size must be > 0"));
        }
        if self.page_count == 0 {
            return Err(ServiceError::input("bad_config", "page_count must be > 0"));
        }
        if self.capacity == 0 {
            return Err(ServiceError::input("bad_config", "capacity must be > 0"));
        }
        if self.max_batch_writes == 0 {
            return Err(ServiceError::input(
                "bad_config",
                "max_batch_writes must be > 0",
            ));
        }
        Ok(())
    }
}

fn env_usize(key: &str, default: usize) -> Result<usize> {
    match std::env::var(key) {
        Ok(v) => v
            .parse::<usize>()
            .map_err(|_| ServiceError::input("bad_config", format!("{key}={v:?} is not a usize"))),
        Err(_) => Ok(default),
    }
}
