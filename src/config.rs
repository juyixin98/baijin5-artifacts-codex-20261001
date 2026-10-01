//! Configuration layer. Precedence (lowest to highest):
//! built-in defaults -> optional TOML file -> environment variables.
//! Nothing security-sensitive is hardcoded; the server binds localhost by
//! default and needs no external accounts.

use std::path::Path;

use serde::Deserialize;

use crate::error::{Error, Result};

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct Config {
    #[serde(default)]
    pub server: ServerConfig,
    #[serde(default)]
    pub log: LogConfig,
    #[serde(default)]
    pub query: QueryConfig,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(default)]
pub struct ServerConfig {
    pub host: String,
    pub port: u16,
}

impl Default for ServerConfig {
    fn default() -> Self {
        Self {
            host: "127.0.0.1".to_string(),
            port: 8080,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(default)]
pub struct LogConfig {
    /// `error` | `warn` | `info` | `debug` | `trace`.
    pub level: String,
    /// Include the step trace in query API responses.
    pub include_trace: bool,
}

impl Default for LogConfig {
    fn default() -> Self {
        Self {
            level: "info".to_string(),
            include_trace: true,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(default)]
pub struct QueryConfig {
    /// Maximum expression nodes accepted in one filter request.
    pub max_expr_nodes: usize,
    /// Maximum rows accepted in one load/append call.
    pub max_append_rows: usize,
}

impl Default for QueryConfig {
    fn default() -> Self {
        Self {
            max_expr_nodes: 10_000,
            max_append_rows: 1_000_000,
        }
    }
}

impl Config {
    pub fn default_values() -> Self {
        Self {
            server: ServerConfig::default(),
            log: LogConfig::default(),
            query: QueryConfig::default(),
        }
    }

    /// Load: defaults, then TOML file if present, then env overrides.
    pub fn load(path: Option<&Path>) -> Result<Self> {
        let mut cfg = Config::default_values();
        if let Some(p) = path {
            if p.exists() {
                let text = std::fs::read_to_string(p).map_err(|e| {
                    Error::invalid(format!("cannot read config {}: {e}", p.display()))
                })?;
                cfg = toml::from_str(&text)
                    .map_err(|e| Error::invalid(format!("bad TOML in {}: {e}", p.display())))?;
            }
        }
        cfg.apply_env()?;
        Ok(cfg)
    }

    fn apply_env(&mut self) -> Result<()> {
        if let Ok(v) = std::env::var("TRIBOOL_HOST") {
            self.server.host = v;
        }
        if let Ok(v) = std::env::var("TRIBOOL_PORT") {
            self.server.port = v
                .parse()
                .map_err(|_| Error::invalid(format!("TRIBOOL_PORT='{v}' is not a valid port")))?;
        }
        if let Ok(v) = std::env::var("TRIBOOL_LOG_LEVEL") {
            self.log.level = v;
        }
        if let Ok(v) = std::env::var("TRIBOOL_INCLUDE_TRACE") {
            self.log.include_trace = v.parse().map_err(|_| {
                Error::invalid(format!("TRIBOOL_INCLUDE_TRACE='{v}' must be true/false"))
            })?;
        }
        if let Ok(v) = std::env::var("TRIBOOL_MAX_EXPR_NODES") {
            self.query.max_expr_nodes = v.parse().map_err(|_| {
                Error::invalid(format!("TRIBOOL_MAX_EXPR_NODES='{v}' must be a usize"))
            })?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_are_localhost_and_safe() {
        let cfg = Config::default_values();
        assert_eq!(cfg.server.host, "127.0.0.1");
        assert_eq!(cfg.server.port, 8080);
        assert!(cfg.log.include_trace);
        assert!(cfg.query.max_expr_nodes > 0);
    }

    #[test]
    fn parses_partial_toml() {
        let cfg: Config = toml::from_str("[server]\nport = 9090\n").unwrap();
        assert_eq!(cfg.server.port, 9090);
        // Defaults fill omitted sections.
        assert_eq!(cfg.server.host, "127.0.0.1");
        assert_eq!(cfg.log.level, "info");
    }

    #[test]
    fn bad_port_env_surfaces_invalid_input() {
        std::env::set_var("TRIBOOL_PORT", "not-a-port");
        let err = Config::default_values().apply_env();
        std::env::remove_var("TRIBOOL_PORT");
        assert!(err.is_err());
    }
}
