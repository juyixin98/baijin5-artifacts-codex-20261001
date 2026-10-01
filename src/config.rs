//! Service configuration, sourced from environment variables with safe local
//! defaults. No production credentials are involved.

use std::env;

/// Process configuration.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AppConfig {
    /// Bind address for the HTTP server.
    pub bind_addr: String,
    /// Maximum accepted request body size in bytes.
    pub max_body_bytes: usize,
    /// Tracing filter (e.g. `info`, `decorrelate_svc=debug`).
    pub log_filter: String,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            bind_addr: "127.0.0.1:8080".to_string(),
            max_body_bytes: 4 * 1024 * 1024,
            log_filter: "info".to_string(),
        }
    }
}

impl AppConfig {
    pub fn from_env() -> Self {
        let d = Self::default();
        Self {
            bind_addr: env::var("DECORR_BIND").unwrap_or(d.bind_addr),
            max_body_bytes: env::var("DECORR_MAX_BODY_BYTES")
                .ok()
                .and_then(|s| s.parse().ok())
                .unwrap_or(d.max_body_bytes),
            log_filter: env::var("DECORR_LOG").unwrap_or(d.log_filter),
        }
    }
}
