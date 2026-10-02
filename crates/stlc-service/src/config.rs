//! Configuration layer: defaults, optional JSON file, environment overrides.
//!
//! Keys: `bind` (`IP:PORT`), `budget` (default reduction budget),
//! `log_dir` (directory for per-run JSONL records).
//! Overrides: `STLC_BIND`, `STLC_BUDGET`, `STLC_LOG_DIR`.

use serde::{Deserialize, Serialize};
use std::path::Path;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Config {
    pub bind: String,
    pub budget: usize,
    pub log_dir: String,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            bind: "127.0.0.1:8208".to_string(),
            budget: 4096,
            log_dir: "logs".to_string(),
        }
    }
}

impl Config {
    /// Load defaults, layer file values (when present), then env overrides.
    pub fn load(path: Option<&Path>) -> Result<Config, String> {
        let mut cfg = Config::default();
        if let Some(path) = path {
            if path.exists() {
                let bytes = std::fs::read(path).map_err(|e| format!("read config: {e}"))?;
                let file_cfg: FileConfig =
                    serde_json::from_slice(&bytes).map_err(|e| format!("parse config: {e}"))?;
                if let Some(v) = file_cfg.bind {
                    cfg.bind = v;
                }
                if let Some(v) = file_cfg.budget {
                    cfg.budget = v;
                }
                if let Some(v) = file_cfg.log_dir {
                    cfg.log_dir = v;
                }
            }
        }
        if let Ok(v) = std::env::var("STLC_BIND") {
            cfg.bind = v;
        }
        if let Ok(v) = std::env::var("STLC_BUDGET") {
            cfg.budget = v.parse().map_err(|_| "STLC_BUDGET must be an integer")?;
        }
        if let Ok(v) = std::env::var("STLC_LOG_DIR") {
            cfg.log_dir = v;
        }
        Ok(cfg)
    }
}

#[derive(Debug, Clone, Default, Deserialize)]
struct FileConfig {
    #[serde(default)]
    bind: Option<String>,
    #[serde(default)]
    budget: Option<usize>,
    #[serde(default)]
    log_dir: Option<String>,
}
