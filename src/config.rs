//! Configuration layer: TOML file + environment overrides.
//!
//! Resolution order (first hit wins):
//! 1. path in env var `RIB_CONFIG`
//! 2. `config/default.toml` relative to the working directory
//! 3. built-in defaults

use serde::Deserialize;
use std::path::Path;

use crate::budget::DecodeBudget;

/// Environment variable naming the config file.
pub const CONFIG_ENV: &str = "RIB_CONFIG";

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct ServerConfig {
    pub host: String,
    pub port: u16,
}

impl Default for ServerConfig {
    fn default() -> Self {
        ServerConfig {
            host: "127.0.0.1".to_string(),
            port: 8080,
        }
    }
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default)]
pub struct AppConfig {
    pub server: ServerConfig,
    pub budget: DecodeBudget,
}

impl AppConfig {
    /// Load configuration from the standard locations, falling back to
    /// defaults when no file exists. A malformed file is an error, not a
    /// silent fallback.
    pub fn load() -> Result<AppConfig, String> {
        let path = std::env::var(CONFIG_ENV)
            .unwrap_or_else(|_| "config/default.toml".to_string());
        if !Path::new(&path).exists() {
            return Ok(AppConfig::default());
        }
        let text = std::fs::read_to_string(&path)
            .map_err(|e| format!("cannot read config {}: {}", path, e))?;
        toml::from_str(&text).map_err(|e| format!("invalid config {}: {}", path, e))
    }
}
