//! Service configuration, loaded from TOML.

use std::path::{Path, PathBuf};

use serde::Deserialize;

use crate::engine::EngineConfig;

#[derive(Clone, Debug, Deserialize)]
pub struct Config {
    /// Address the diagnostic HTTP interface binds to.
    pub bind_addr: String,
    /// Directory holding the snapshot journal (persistent sampling state).
    pub data_dir: PathBuf,
    /// Maximum cumulative CPU counter value before wraparound.
    pub counter_max: u64,
    /// Largest backwards counter jump still explainable as wraparound.
    pub wrap_max_plausible_delta: u64,
}

impl Config {
    pub fn load(path: &Path) -> Result<Config, String> {
        let raw = std::fs::read_to_string(path)
            .map_err(|e| format!("cannot read {}: {e}", path.display()))?;
        toml::from_str(&raw).map_err(|e| format!("cannot parse {}: {e}", path.display()))
    }

    pub fn engine_config(&self) -> EngineConfig {
        EngineConfig {
            counter_max: self.counter_max,
            wrap_max_plausible_delta: self.wrap_max_plausible_delta,
        }
    }
}
