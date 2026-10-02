use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

/// Service configuration: sampling and counter-classification knobs plus
/// persistence and HTTP listener settings.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Config {
    /// Address the diagnostic HTTP API listens on.
    pub listen: String,
    /// Directory where sampling state (`state.json`) is persisted.
    pub data_dir: PathBuf,
    /// Kernel clock tick rate (jiffies per second) of the sampled system.
    pub hz: u64,
    /// Counter modulus used to recognise counter wraparound
    /// (2^32 for 32-bit counters, 2^64 for 64-bit).
    pub counter_modulus: u64,
    /// Maximum CPU jiffies a single process may plausibly consume between two
    /// adjacent samples. A counter regression whose wrap-adjusted delta stays
    /// within `elapsed_intervals * max_jiffies_per_interval` is classified as
    /// a wraparound; anything larger is a data anomaly.
    pub max_jiffies_per_interval: u64,
    /// Ring-buffer capacity for diagnostic records.
    pub max_diag_records: usize,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            listen: "127.0.0.1:9485".to_string(),
            data_dir: PathBuf::from("./data"),
            hz: 100,
            counter_modulus: 1u64 << 32,
            max_jiffies_per_interval: 200,
            max_diag_records: 256,
        }
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ConfigError {
    #[error("cannot read config file {path}: {source}")]
    Io {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("cannot parse config file {path}: {source}")]
    Parse {
        path: PathBuf,
        #[source]
        source: toml::de::Error,
    },
}

impl Config {
    pub fn load(path: &Path) -> Result<Self, ConfigError> {
        let text = std::fs::read_to_string(path).map_err(|source| ConfigError::Io {
            path: path.to_path_buf(),
            source,
        })?;
        toml::from_str(&text).map_err(|source| ConfigError::Parse {
            path: path.to_path_buf(),
            source,
        })
    }

    /// Load config from `path`, falling back to defaults when the file does
    /// not exist. Parse/IO errors other than "not found" are fatal.
    pub fn load_or_default(path: &Path) -> Result<Self, ConfigError> {
        match Self::load(path) {
            Ok(cfg) => Ok(cfg),
            Err(ConfigError::Io { source, .. }) if source.kind() == std::io::ErrorKind::NotFound => {
                Ok(Self::default())
            }
            Err(err) => Err(err),
        }
    }
}
