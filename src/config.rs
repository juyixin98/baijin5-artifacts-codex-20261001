//! Typed configuration loaded from TOML, overridable by environment variables.
//!
//! See `config/tvindex.toml` for the documented example. Environment
//! overrides use the `TVINDEX__` prefix and `__` as a section separator, e.g.
//! `TVINDEX__SERVER__PORT=9090`.

use std::path::{Path, PathBuf};

use serde::Deserialize;

use crate::error::{Result, TviError};

/// Root configuration.
#[derive(Clone, Debug, Deserialize)]
pub struct Config {
    pub server: ServerConfig,
    pub data: DataConfig,
    pub log: LogConfig,
}

/// HTTP listener settings.
#[derive(Clone, Debug, Deserialize)]
pub struct ServerConfig {
    pub host: String,
    pub port: u16,
    /// Per-request body size limit in bytes.
    pub max_body_bytes: usize,
}

/// Where fixtures and manifests live.
#[derive(Clone, Debug, Deserialize)]
pub struct DataConfig {
    /// Directory containing `*.csv` fixtures.
    pub fixture_dir: PathBuf,
    /// Manifest file declaring the typed schema and deletes.
    pub manifest: PathBuf,
}

/// Observability settings.
#[derive(Clone, Debug, Deserialize)]
pub struct LogConfig {
    /// error|warn|info|debug|trace
    pub level: String,
    /// Whether each response also carries the per-row trace.
    pub include_trace: bool,
}

impl Config {
    /// Load from an optional TOML path (defaults to `config/tvindex.toml`)
    /// and apply environment overrides.
    pub fn load(explicit: Option<&Path>) -> Result<Self> {
        let path = explicit
            .map(Path::to_path_buf)
            .or_else(|| Some(PathBuf::from("config/tvindex.toml")))
            .expect("path present");
        let text = std::fs::read_to_string(&path)
            .map_err(|e| TviError::Io(format!("reading config {}: {e}", path.display())))?;
        let mut cfg: Config = toml::from_str(&text)
            .map_err(|e| TviError::Io(format!("parsing config {}: {e}", path.display())))?;

        if let Ok(host) = std::env::var("TVINDEX__SERVER__HOST") {
            cfg.server.host = host;
        }
        if let Ok(port) = std::env::var("TVINDEX__SERVER__PORT") {
            cfg.server.port = port
                .parse()
                .map_err(|e| TviError::Io(format!("TVINDEX__SERVER__PORT not a u16: {e}")))?;
        }
        if let Ok(dir) = std::env::var("TVINDEX__DATA__FIXTURE_DIR") {
            cfg.data.fixture_dir = PathBuf::from(dir);
        }
        if let Ok(level) = std::env::var("TVINDEX__LOG__LEVEL") {
            cfg.log.level = level;
        }
        Ok(cfg)
    }

    /// Effective listener address.
    pub fn bind_addr(&self) -> String {
        format!("{}:{}", self.server.host, self.server.port)
    }
}

impl Default for Config {
    fn default() -> Self {
        Config {
            server: ServerConfig {
                host: "127.0.0.1".into(),
                port: 8080,
                max_body_bytes: 1 << 20,
            },
            data: DataConfig {
                fixture_dir: PathBuf::from("fixtures"),
                manifest: PathBuf::from("fixtures/manifest.toml"),
            },
            log: LogConfig {
                level: "info".into(),
                include_trace: true,
            },
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_example_config() {
        let cfg = Config::load(Some(Path::new("config/tvindex.toml"))).unwrap();
        assert!(cfg.server.port > 0);
        assert!(cfg.server.max_body_bytes > 0);
        assert!(cfg.log.level.chars().all(|c| c.is_ascii_alphanumeric()));
    }
}
