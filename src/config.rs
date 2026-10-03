//! Runtime configuration, loaded from TOML with explicit defaults.

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct RuntimeConfig {
    /// Maximum number of in-flight submissions; overflow is backpressured.
    pub capacity: usize,
    /// Number of data buffers in the pool.
    pub buffer_count: usize,
    /// Default per-submission timeout (ms) when the caller passes 0.
    pub default_timeout_ms: u64,
    /// Path of the JSONL completion journal.
    pub journal_path: PathBuf,
    /// Journal sampling: keep 1 of every n finalized records.
    pub journal_sample_n: u32,
    /// Listen address of the diagnostics/demo HTTP server.
    pub diag_addr: String,
}

impl Default for RuntimeConfig {
    fn default() -> Self {
        Self {
            capacity: 8,
            buffer_count: 8,
            default_timeout_ms: 5_000,
            journal_path: PathBuf::from("var/journal.jsonl"),
            journal_sample_n: 1,
            diag_addr: "127.0.0.1:7878".to_string(),
        }
    }
}

impl RuntimeConfig {
    /// Load from a TOML file, or return defaults when `path` is `None`.
    pub fn load(path: Option<&Path>) -> Result<Self, ConfigError> {
        let Some(path) = path else {
            return Ok(Self::default());
        };
        let text = std::fs::read_to_string(path).map_err(|e| ConfigError::Read {
            path: path.to_path_buf(),
            message: e.to_string(),
        })?;
        toml::from_str(&text).map_err(|e| ConfigError::Parse {
            path: path.to_path_buf(),
            message: e.to_string(),
        })
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ConfigError {
    #[error("cannot read config {}: {message}", path.display())]
    Read { path: PathBuf, message: String },
    #[error("cannot parse config {}: {message}", path.display())]
    Parse { path: PathBuf, message: String },
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_are_sensible() {
        let cfg = RuntimeConfig::default();
        assert_eq!(cfg.capacity, 8);
        assert_eq!(cfg.journal_sample_n, 1);
    }

    #[test]
    fn partial_toml_fills_defaults() {
        let cfg: RuntimeConfig =
            toml::from_str("capacity = 2\njournal_sample_n = 5").expect("parse");
        assert_eq!(cfg.capacity, 2);
        assert_eq!(cfg.journal_sample_n, 5);
        assert_eq!(cfg.buffer_count, 8, "default preserved");
    }
}
