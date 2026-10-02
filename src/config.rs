//! Startup configuration (TOML). See config/default.toml.

use std::path::PathBuf;

use serde::Deserialize;

use crate::error::BackendError;

#[derive(Debug, Clone, Deserialize)]
pub struct Limits {
    pub max_runs: usize,
    pub max_samples_per_run: usize,
    pub max_stack_depth: usize,
    pub max_samples_per_request: usize,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Config {
    pub bind: String,
    pub data_dir: PathBuf,
    pub limits: Limits,
}

impl Config {
    pub fn load(path: &str) -> Result<Self, BackendError> {
        let text = std::fs::read_to_string(path).map_err(|e| {
            BackendError::input("config_unreadable", format!("cannot read {path}: {e}"))
        })?;
        toml::from_str(&text).map_err(|e| {
            BackendError::input("config_invalid", format!("cannot parse {path}: {e}"))
        })
    }
}
