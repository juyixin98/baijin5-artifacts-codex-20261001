//! Startup configuration, loaded from a TOML file with sane defaults.

use crate::chunker::ChunkParams;
use crate::limits::Limits;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServiceConfig {
    pub listen: String,
    #[serde(default)]
    pub limits: Limits,
    #[serde(default)]
    pub chunker: ChunkParams,
}

impl Default for ServiceConfig {
    fn default() -> Self {
        Self {
            listen: "127.0.0.1:8080".to_string(),
            limits: Limits::default(),
            chunker: ChunkParams::default(),
        }
    }
}

impl ServiceConfig {
    pub fn load(path: Option<&str>) -> Result<Self, String> {
        match path {
            None => Ok(Self::default()),
            Some(p) => {
                let text = std::fs::read_to_string(p)
                    .map_err(|e| format!("cannot read config {p}: {e}"))?;
                let cfg: ServiceConfig =
                    toml::from_str(&text).map_err(|e| format!("cannot parse config {p}: {e}"))?;
                cfg.chunker
                    .validate()
                    .map_err(|e| format!("invalid chunker params in {p}: {e}"))?;
                Ok(cfg)
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_example_config() {
        let toml_text = r#"
listen = "127.0.0.1:18080"

[limits]
max_input_bytes = 1048576
max_body_bytes = 2097152
max_chunks = 1000

[chunker]
min_size = 1024
max_size = 32768
mask_bits = 12
pattern = 0
"#;
        let cfg: ServiceConfig = toml::from_str(toml_text).unwrap();
        assert_eq!(cfg.listen, "127.0.0.1:18080");
        assert_eq!(cfg.chunker.min_size, 1024);
        assert_eq!(cfg.limits.max_chunks, 1000);
        cfg.chunker.validate().unwrap();
    }
}
