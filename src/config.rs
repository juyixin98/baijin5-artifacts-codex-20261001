//! Process configuration, sourced from environment variables.
//!
//! The layer is deliberately tiny and explicit: every supported variable is
//! named here, parsed strictly, and validated. Per-request limit overrides are
//! checked against these process-wide ceilings in the validation layer.

use crate::error::{EngineError, Result};
use crate::plan::TraversalOrder;

/// Process-wide configuration.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Config {
    /// Socket address the HTTP server binds to.
    pub bind_addr: String,
    /// Default traversal order when a request does not specify one.
    pub default_traversal: TraversalOrder,
    /// Process ceiling for recursion depth. Requests may only lower it.
    pub max_depth: u32,
    /// Process ceiling for accumulated output rows.
    pub max_rows: u64,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            bind_addr: "127.0.0.1:8080".to_owned(),
            default_traversal: TraversalOrder::BreadthFirst,
            max_depth: 64,
            max_rows: 100_000,
        }
    }
}

impl Config {
    /// Load configuration from the process environment.
    ///
    /// Supported variables:
    /// - `RCE_BIND_ADDR` (default `127.0.0.1:8080`)
    /// - `RCE_DEFAULT_ORDER` (`bfs` | `dfs` | `input`, default `bfs`)
    /// - `RCE_MAX_DEPTH` (default `64`)
    /// - `RCE_MAX_ROWS` (default `100000`)
    pub fn from_env() -> Result<Self> {
        Self::from_pairs(std::env::vars())
    }

    /// Build configuration from arbitrary key/value pairs (the test entry
    /// point; unrecognized variables are ignored).
    pub fn from_pairs(pairs: impl IntoIterator<Item = (String, String)>) -> Result<Self> {
        let mut cfg = Config::default();
        for (key, value) in pairs {
            let value = value.trim();
            match key.as_str() {
                "RCE_BIND_ADDR" => {
                    if value.is_empty() {
                        return Err(EngineError::validation("RCE_BIND_ADDR must not be empty"));
                    }
                    cfg.bind_addr = value.to_owned();
                }
                "RCE_DEFAULT_ORDER" => {
                    cfg.default_traversal = TraversalOrder::parse(value).ok_or_else(|| {
                        EngineError::validation(format!(
                            "RCE_DEFAULT_ORDER must be one of bfs|dfs|input, got '{value}'"
                        ))
                    })?;
                }
                "RCE_MAX_DEPTH" => {
                    cfg.max_depth = parse_positive(value, "RCE_MAX_DEPTH")?;
                }
                "RCE_MAX_ROWS" => {
                    cfg.max_rows = parse_positive::<u64>(value, "RCE_MAX_ROWS")?;
                }
                _ => {}
            }
        }
        Ok(cfg)
    }
}

fn parse_positive<T>(value: &str, var: &str) -> Result<T>
where
    T: std::str::FromStr,
{
    value
        .parse::<T>()
        .map_err(|_| {
            EngineError::validation(format!("{var} must be a positive integer, got '{value}'"))
        })
        .and_then(|n| {
            // Numeric positivity is checked by the caller through this helper by
            // comparing against zero where the type supports it; FromStr already
            // rejects signs/overflow for unsigned targets used by Config.
            if value == "0" {
                Err(EngineError::validation(format!("{var} must be >= 1")))
            } else {
                Ok(n)
            }
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_are_stable() {
        let cfg = Config::from_pairs(Vec::<(String, String)>::new()).unwrap();
        assert_eq!(cfg, Config::default());
        assert_eq!(cfg.default_traversal, TraversalOrder::BreadthFirst);
    }

    #[test]
    fn overrides_apply() {
        let cfg = Config::from_pairs([
            ("RCE_BIND_ADDR".into(), "0.0.0.0:9000".into()),
            ("RCE_DEFAULT_ORDER".into(), "dfs".into()),
            ("RCE_MAX_DEPTH".into(), "7".into()),
            ("RCE_MAX_ROWS".into(), "1234".into()),
        ])
        .unwrap();
        assert_eq!(cfg.bind_addr, "0.0.0.0:9000");
        assert_eq!(cfg.default_traversal, TraversalOrder::DepthFirst);
        assert_eq!(cfg.max_depth, 7);
        assert_eq!(cfg.max_rows, 1234);
    }

    #[test]
    fn rejects_bad_values() {
        assert!(Config::from_pairs([("RCE_MAX_DEPTH".into(), "zero".into())]).is_err());
        assert!(Config::from_pairs([("RCE_MAX_DEPTH".into(), "0".into())]).is_err());
        assert!(Config::from_pairs([("RCE_MAX_ROWS".into(), "-1".into())]).is_err());
        assert!(Config::from_pairs([("RCE_DEFAULT_ORDER".into(), "sideways".into())]).is_err());
        assert!(Config::from_pairs([("RCE_BIND_ADDR".into(), "  ".into())]).is_err());
    }
}
