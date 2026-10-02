//! Independent runtime configuration, loadable from JSON.

use serde::{Deserialize, Serialize};

use std::path::Path;

pub const ENGINE_VERSION: &str = env!("CARGO_PKG_VERSION");
pub const ENGINE_NAME: &str = "craig-ipc-workbench";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct ServiceConfig {
    pub site: String,
    pub default_budget: Option<u64>,
    pub include_proof: bool,
    pub http_host: String,
    pub http_port: u16,
    pub log_to_stderr: bool,
}

impl Default for ServiceConfig {
    fn default() -> Self {
        ServiceConfig {
            site: "local-dev-site".to_string(),
            default_budget: Some(5_000),
            include_proof: true,
            http_host: "127.0.0.1".to_string(),
            http_port: 8190,
            log_to_stderr: true,
        }
    }
}

impl ServiceConfig {
    pub fn load_or_default(path: Option<&Path>) -> Result<ServiceConfig, String> {
        match path {
            Some(path) => {
                let text = std::fs::read_to_string(path)
                    .map_err(|error| format!("cannot read config {}: {error}", path.display()))?;
                serde_json::from_str(&text)
                    .map_err(|error| format!("invalid config JSON: {error}"))
            }
            None => Ok(ServiceConfig::default()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_are_stable() {
        let config = ServiceConfig::default();
        assert_eq!(config.http_port, 8190);
        assert!(config.include_proof);
    }

    #[test]
    fn parses_partial_json() {
        let parsed: ServiceConfig = serde_json::from_str(r#"{"http_port": 9000}"#).unwrap();
        assert_eq!(parsed.http_port, 9000);
        assert_eq!(parsed.site, "local-dev-site");
    }
}
