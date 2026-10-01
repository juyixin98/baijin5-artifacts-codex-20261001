//! Shared application state and per-request identity.

use std::sync::Arc;
use std::time::Instant;

use tower_http::limit::RequestBodyLimitLayer;

use crate::config::AppConfig;

/// Process-wide shared state.
#[derive(Debug, Clone)]
pub struct AppState {
    pub config: Arc<AppConfig>,
    /// Service version, surfaced in responses and logs.
    pub version: &'static str,
    /// Human-readable location identifier for this process (host-local only).
    pub location: String,
}

impl AppState {
    pub fn new(config: AppConfig) -> Self {
        let location =
            std::env::var("DECORR_LOCATION").unwrap_or_else(|_| "local-synthetic".to_string());
        Self {
            config: Arc::new(config),
            version: env!("CARGO_PKG_VERSION"),
            location,
        }
    }

    pub fn body_limit_layer(&self) -> RequestBodyLimitLayer {
        RequestBodyLimitLayer::new(self.config.max_body_bytes)
    }
}

/// Per-request identity used to correlate logs with the response.
#[derive(Debug, Clone)]
pub struct RequestCtx {
    pub request_id: String,
    pub started: Instant,
}

impl RequestCtx {
    pub fn new() -> Self {
        Self {
            request_id: uuid::Uuid::new_v4().to_string(),
            started: Instant::now(),
        }
    }

    /// Use a caller-supplied id (e.g. the `x-request-id` header) when present,
    /// keeping response and logs correlated to the client's identifier.
    pub fn with_id(id: impl Into<String>) -> Self {
        Self {
            request_id: id.into(),
            started: Instant::now(),
        }
    }

    pub fn elapsed_ms(&self) -> u128 {
        self.started.elapsed().as_millis()
    }
}

impl Default for RequestCtx {
    fn default() -> Self {
        Self::new()
    }
}
