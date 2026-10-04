//! Configuration layer: file-based config (TOML) with env overrides,
//! shared by the CLI/server and reused by integration tests.

use serde::Deserialize;

use crate::decode::DecodeBudget;
use crate::encode::EncoderConfig;

/// Top-level application configuration.
#[derive(Debug, Clone, Deserialize)]
pub struct AppConfig {
    #[serde(default)]
    pub server: ServerConfig,
    #[serde(default)]
    pub encoder: EncoderConfig,
    #[serde(default)]
    pub budget: DecodeBudget,
    /// Max HTTP request body bytes (encode/decode payloads).
    #[serde(default = "default_max_http_body")]
    pub max_http_body_bytes: usize,
    /// Max values accepted by a single /v1/encode request.
    #[serde(default = "default_max_encode_values")]
    pub max_encode_values: usize,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ServerConfig {
    #[serde(default = "default_host")]
    pub host: String,
    #[serde(default = "default_port")]
    pub port: u16,
}

fn default_host() -> String {
    "127.0.0.1".to_string()
}
fn default_port() -> u16 {
    8080
}
fn default_max_http_body() -> usize {
    1 << 22
}
fn default_max_encode_values() -> usize {
    1 << 22
}

impl Default for ServerConfig {
    fn default() -> Self {
        ServerConfig {
            host: default_host(),
            port: default_port(),
        }
    }
}

impl Default for AppConfig {
    fn default() -> Self {
        AppConfig {
            server: ServerConfig::default(),
            encoder: EncoderConfig::default(),
            budget: DecodeBudget::default(),
            max_http_body_bytes: default_max_http_body(),
            max_encode_values: default_max_encode_values(),
        }
    }
}

impl AppConfig {
    /// Load from a TOML file; missing file falls back to defaults.
    /// `RBP_CONFIG` overrides the path; individual keys can be overridden
    /// with env vars: `RBP_HOST`, `RBP_PORT`.
    pub fn load() -> Result<Self, ConfigError> {
        let path = std::env::var("RBP_CONFIG")
            .unwrap_or_else(|_| "config/default.toml".to_string());
        let mut cfg = match std::fs::read_to_string(&path) {
            Ok(text) => toml::from_str::<AppConfig>(&text)
                .map_err(|e| ConfigError::Parse(path.clone(), e.to_string()))?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => AppConfig::default(),
            Err(e) => return Err(ConfigError::Io(path.clone(), e.to_string())),
        };
        if let Ok(host) = std::env::var("RBP_HOST") {
            cfg.server.host = host;
        }
        if let Ok(port) = std::env::var("RBP_PORT") {
            cfg.server.port = port
                .parse()
                .map_err(|_| ConfigError::Parse("RBP_PORT".into(), "not a u16".into()))?;
        }
        Ok(cfg)
    }
}

/// Configuration load failures — surfaced at startup, never silently ignored.
#[derive(Debug, thiserror::Error)]
pub enum ConfigError {
    #[error("cannot read config file {0}: {1}")]
    Io(String, String),
    #[error("cannot parse config file {0}: {1}")]
    Parse(String, String),
}
