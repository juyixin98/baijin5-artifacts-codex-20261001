//! Runtime configuration, loaded from TOML.

use std::path::{Path, PathBuf};

use serde::Deserialize;

use crate::engine::EngineConfig;

#[derive(Debug, Clone, Deserialize)]
pub struct RuntimeConfig {
    pub bind: String,
    pub queue_capacity: usize,
    pub buffer_capacity: usize,
    pub timeout_ms: u64,
    pub pump_interval_ms: u64,
    pub journal_path: PathBuf,
    pub snapshot_every: u64,
    pub default_io_delay_ms: u64,
}

#[derive(Debug)]
pub enum ConfigError {
    Io(std::io::Error),
    Parse(toml::de::Error),
}

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ConfigError::Io(e) => write!(f, "cannot read config: {e}"),
            ConfigError::Parse(e) => write!(f, "cannot parse config: {e}"),
        }
    }
}

impl std::error::Error for ConfigError {}

impl RuntimeConfig {
    pub fn load(path: impl AsRef<Path>) -> Result<Self, ConfigError> {
        let text = std::fs::read_to_string(path).map_err(ConfigError::Io)?;
        toml::from_str(&text).map_err(ConfigError::Parse)
    }

    pub fn engine_config(&self) -> EngineConfig {
        EngineConfig {
            queue_capacity: self.queue_capacity,
            buffer_capacity: self.buffer_capacity,
            timeout_ms: self.timeout_ms,
            snapshot_every: self.snapshot_every,
        }
    }
}
