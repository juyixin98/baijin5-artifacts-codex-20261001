//! Independent configuration layer.
//!
//! Server/runtime settings are completely separate from per-query plans: they
//! come from environment variables with validated defaults and fail fast on
//! malformed values. Nothing here influences query semantics.

use std::env;

use crate::error::{EngineError, EngineResult};

/// Process-level runtime configuration.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ServerConfig {
    pub bind_host: String,
    pub bind_port: u16,
    /// Reject requests whose JSON body exceeds this many bytes.
    pub max_request_bytes: usize,
    /// Default depth bound when a request omits `limits`.
    pub default_max_depth: usize,
    /// Default row bound when a request omits `limits`.
    pub default_max_rows: usize,
    /// Hard ceiling a request may never exceed, regardless of its own limits.
    pub hard_max_rows: usize,
    pub log_format: LogFormat,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LogFormat {
    Json,
    Text,
}

impl Default for ServerConfig {
    fn default() -> Self {
        Self {
            bind_host: "127.0.0.1".to_string(),
            bind_port: 8080,
            max_request_bytes: 4 * 1024 * 1024,
            default_max_depth: 64,
            default_max_rows: 10_000,
            hard_max_rows: 1_000_000,
            log_format: LogFormat::Text,
        }
    }
}

impl ServerConfig {
    /// Build from the process environment, validating every parse.
    pub fn from_env() -> EngineResult<Self> {
        let mut cfg = Self::default();
        if let Ok(v) = env::var("CTE_HOST") {
            if v.is_empty() {
                return Err(config_err("CTE_HOST must not be empty"));
            }
            cfg.bind_host = v;
        }
        if let Ok(v) = env::var("CTE_PORT") {
            cfg.bind_port = parse_bounded("CTE_PORT", &v, 1, u16::MAX as usize)? as u16;
        }
        if let Ok(v) = env::var("CTE_MAX_REQUEST_BYTES") {
            cfg.max_request_bytes = parse_bounded("CTE_MAX_REQUEST_BYTES", &v, 1024, 1 << 30)?;
        }
        if let Ok(v) = env::var("CTE_DEFAULT_MAX_DEPTH") {
            cfg.default_max_depth = parse_bounded("CTE_DEFAULT_MAX_DEPTH", &v, 1, 1_000_000)?;
        }
        if let Ok(v) = env::var("CTE_DEFAULT_MAX_ROWS") {
            cfg.default_max_rows = parse_bounded("CTE_DEFAULT_MAX_ROWS", &v, 1, 1_000_000)?;
        }
        if let Ok(v) = env::var("CTE_HARD_MAX_ROWS") {
            cfg.hard_max_rows = parse_bounded("CTE_HARD_MAX_ROWS", &v, 1, 10_000_000)?;
        }
        if let Ok(v) = env::var("CTE_LOG_FORMAT") {
            cfg.log_format = match v.as_str() {
                "json" => LogFormat::Json,
                "text" => LogFormat::Text,
                other => {
                    return Err(config_err(format!(
                        "CTE_LOG_FORMAT must be 'json' or 'text', got '{other}'"
                    )))
                }
            };
        }
        Ok(cfg)
    }

    /// Apply server-wide ceilings to a request, filling omitted limits.
    pub fn enforce(&self, req: &mut crate::plan::RecursiveRequest) -> EngineResult<()> {
        if req.limits.max_depth == 0 {
            req.limits.max_depth = self.default_max_depth;
        }
        if req.limits.max_rows == 0 {
            req.limits.max_rows = self.default_max_rows;
        }
        if req.limits.max_rows > self.hard_max_rows {
            return Err(EngineError::resource_limit(format!(
                "limits.max_rows={} exceeds server hard ceiling {}",
                req.limits.max_rows, self.hard_max_rows
            )));
        }
        Ok(())
    }
}

fn parse_bounded(name: &str, raw: &str, min: usize, max: usize) -> EngineResult<usize> {
    let value = raw.parse::<usize>().map_err(|_| {
        config_err(format!(
            "{name}='{raw}' is not a valid non-negative integer"
        ))
    })?;
    if value < min || value > max {
        return Err(config_err(format!(
            "{name}={value} outside allowed range [{min}, {max}]"
        )));
    }
    Ok(value)
}

fn config_err(msg: impl Into<String>) -> EngineError {
    // Configuration is a server-side resource/validation problem.
    EngineError::resource_limit(msg)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_are_valid() {
        let cfg = ServerConfig::default();
        assert!(cfg.default_max_depth >= 1);
        assert!(cfg.default_max_rows >= 1);
        assert!(cfg.hard_max_rows >= cfg.default_max_rows);
    }

    #[test]
    fn rejects_garbage_port() {
        assert!(parse_bounded("CTE_PORT", "not-a-port", 1, 65535).is_err());
        assert!(parse_bounded("CTE_PORT", "0", 1, 65535).is_err());
        assert!(parse_bounded("CTE_PORT", "70000", 1, 65535).is_err());
    }
}
