//! Process configuration, sourced from environment variables with explicit
//! defaults. All limits live here rather than being scattered as magic
//! numbers.

use std::time::Duration;

/// Immutable service configuration.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Config {
    /// Socket address the HTTP server binds to.
    pub bind_addr: String,
    /// Maximum relations accepted in one natural join (restricted envelope).
    pub max_relations: usize,
    /// Default output-row cap when the request omits one.
    pub default_limit: u64,
    /// Hard ceiling on a single page regardless of what clients request.
    pub max_limit: u64,
    /// Upper bound on trie accesses when `access_budget` is requested; `None`
    /// here disables budgeted (possibly-incomplete) runs server-side.
    pub max_access_budget: Option<u64>,
    /// How long resumption cursors stay valid.
    pub cursor_ttl: Duration,
    /// Maximum number of live cursors retained (oldest evicted first).
    pub max_cursors: usize,
}

impl Config {
    /// Build from the process environment, applying defaults on absence and
    /// rejecting malformed values explicitly.
    pub fn from_env() -> crate::error::Result<Self> {
        Ok(Config {
            bind_addr: env_string("LFTJ_BIND_ADDR", "127.0.0.1:8080"),
            max_relations: env_usize("LFTJ_MAX_RELATIONS", 6)?,
            default_limit: env_u64("LFTJ_DEFAULT_LIMIT", 1024)?,
            max_limit: env_u64("LFTJ_MAX_LIMIT", 100_000)?,
            max_access_budget: match std::env::var("LFTJ_MAX_ACCESS_BUDGET").ok() {
                Some(raw) if !raw.is_empty() => Some(parse_u64("LFTJ_MAX_ACCESS_BUDGET", &raw)?),
                _ => Some(100_000_000),
            },
            cursor_ttl: Duration::from_secs(env_u64("LFTJ_CURSOR_TTL_SECS", 300)?),
            max_cursors: env_usize("LFTJ_MAX_CURSORS", 1024)?,
        })
    }
}

impl Default for Config {
    fn default() -> Self {
        Config {
            bind_addr: "127.0.0.1:8080".to_string(),
            max_relations: 6,
            default_limit: 1024,
            max_limit: 100_000,
            max_access_budget: Some(100_000_000),
            cursor_ttl: Duration::from_secs(300),
            max_cursors: 1024,
        }
    }
}

fn env_string(key: &str, default: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| default.to_string())
}

fn env_u64(key: &str, default: u64) -> crate::error::Result<u64> {
    match std::env::var(key) {
        Ok(raw) => parse_u64(key, &raw),
        Err(_) => Ok(default),
    }
}

fn env_usize(key: &str, default: usize) -> crate::error::Result<usize> {
    match std::env::var(key) {
        Ok(raw) => raw.parse::<usize>().map_err(|_| {
            ServiceError::request(format!(
                "environment variable {key}='{raw}' is not a valid size"
            ))
        }),
        Err(_) => Ok(default),
    }
}

fn parse_u64(key: &str, raw: &str) -> crate::error::Result<u64> {
    raw.parse::<u64>().map_err(|_| {
        ServiceError::request(format!(
            "environment variable {key}='{raw}' is not a valid integer"
        ))
    })
}

use crate::error::ServiceError;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_are_coherent() {
        let cfg = Config::default();
        assert!(cfg.default_limit <= cfg.max_limit);
        assert!(cfg.max_relations >= 3, "three-table joins must be allowed");
        assert!(cfg.cursor_ttl.as_secs() > 0);
    }
}
