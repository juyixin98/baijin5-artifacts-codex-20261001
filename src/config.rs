//! Configuration layer: model parameters and server settings.
//!
//! Defaults are usable as-is; a TOML file and environment variables can
//! override them. Kept separate from the model so tests can construct small
//! page sizes while the server uses realistic ones.

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

/// Parameters of the page model itself.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct ModelConfig {
    /// Page size in bytes. Must be a power of two >= 64.
    pub page_size: u64,
    /// Hard cap on file size, guards against runaway allocations.
    pub max_file_size: u64,
    /// Hard cap on simultaneously live mappings.
    pub max_mappings: usize,
}

impl Default for ModelConfig {
    fn default() -> Self {
        ModelConfig {
            page_size: 4096,
            max_file_size: 64 * 1024 * 1024,
            max_mappings: 1024,
        }
    }
}

impl ModelConfig {
    pub fn validate(&self) -> Result<(), String> {
        if self.page_size < 64 || !self.page_size.is_power_of_two() {
            return Err(format!(
                "page_size must be a power of two >= 64, got {}",
                self.page_size
            ));
        }
        if self.max_file_size < self.page_size {
            return Err("max_file_size must be >= page_size".to_string());
        }
        if self.max_mappings == 0 {
            return Err("max_mappings must be > 0".to_string());
        }
        Ok(())
    }
}

/// Server-level configuration (diagnostic interface).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct ServerConfig {
    pub bind: String,
    pub storage_dir: PathBuf,
    pub model: ModelConfig,
}

impl Default for ServerConfig {
    fn default() -> Self {
        ServerConfig {
            bind: "127.0.0.1:9393".to_string(),
            storage_dir: PathBuf::from("./data"),
            model: ModelConfig::default(),
        }
    }
}

impl ServerConfig {
    /// Load from an optional TOML path, then apply env overrides
    /// (`MMAP_MODEL_BIND`, `MMAP_MODEL_STORAGE_DIR`).
    pub fn load(path: Option<&str>) -> Result<Self, String> {
        let mut cfg = match path {
            Some(p) => {
                let text = std::fs::read_to_string(p)
                    .map_err(|e| format!("cannot read config {p}: {e}"))?;
                toml::from_str(&text).map_err(|e| format!("invalid config {p}: {e}"))?
            }
            None => ServerConfig::default(),
        };
        if let Ok(bind) = std::env::var("MMAP_MODEL_BIND") {
            cfg.bind = bind;
        }
        if let Ok(dir) = std::env::var("MMAP_MODEL_STORAGE_DIR") {
            cfg.storage_dir = PathBuf::from(dir);
        }
        cfg.model.validate()?;
        Ok(cfg)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_config_is_valid() {
        ModelConfig::default().validate().unwrap();
    }

    #[test]
    fn rejects_non_power_of_two_page_size() {
        let cfg = ModelConfig {
            page_size: 1000,
            ..ModelConfig::default()
        };
        assert!(cfg.validate().is_err());
    }

    #[test]
    fn parses_toml() {
        let text = r#"
            bind = "127.0.0.1:1234"
            storage_dir = "/tmp/x"
            [model]
            page_size = 512
        "#;
        let cfg: ServerConfig = toml::from_str(text).unwrap();
        assert_eq!(cfg.bind, "127.0.0.1:1234");
        assert_eq!(cfg.model.page_size, 512);
        // unspecified keys fall back to defaults
        assert_eq!(cfg.model.max_mappings, 1024);
    }
}
