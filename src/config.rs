//! TOML configuration. All fields have defaults so an absent config file
//! yields a working (small, in-temp-dir) engine.

use std::path::{Path, PathBuf};

use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct Config {
    /// Cache capacity in pages. Zero is defined: read/write-through.
    pub capacity: usize,
    /// Directory holding `page-<id>.bin` files (the local fixture store).
    pub page_store_dir: PathBuf,
    /// Where snapshots are written / restored from.
    pub snapshot_path: PathBuf,
    /// Listen address for `serve`.
    pub listen: String,
    /// Log raw page ids in diagnostics instead of redacted fingerprints.
    pub log_raw_keys: bool,
    /// Number of diagnostic records retained.
    pub diag_capacity: usize,
    /// Take a stats sample every N requests.
    pub sample_interval: u64,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            capacity: 64,
            page_store_dir: PathBuf::from("data/pages"),
            snapshot_path: PathBuf::from("data/snapshot.json"),
            listen: "127.0.0.1:8080".into(),
            log_raw_keys: false,
            diag_capacity: 256,
            sample_interval: 16,
        }
    }
}

impl Config {
    pub fn load(path: &Path) -> Result<Self, String> {
        let text = std::fs::read_to_string(path)
            .map_err(|e| format!("read {}: {e}", path.display()))?;
        toml::from_str(&text).map_err(|e| format!("parse {}: {e}", path.display()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_partial_toml_with_defaults() {
        let cfg: Config = toml::from_str("capacity = 0\nlog_raw_keys = true\n").unwrap();
        assert_eq!(cfg.capacity, 0);
        assert!(cfg.log_raw_keys);
        assert_eq!(cfg.diag_capacity, 256);
    }
}
