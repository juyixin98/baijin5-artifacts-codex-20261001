//! Axum HTTP surface.
//!
//! - `GET  /health` liveness
//! - `POST /query`  one-shot grouped aggregation (JSON in, JSON out)
//!
//! Validation failures -> `400`; undecidable values (NaN) -> `422`;
//! resource-policy failures -> `507`. Cooperative cancellation -> `200` with
//! `status: "cancelled"` and a resume token in `diagnostics.resume`.

pub mod handlers;

use std::sync::Arc;

use axum::routing::{get, post};
use axum::Router;

use crate::config::Config;

#[derive(Clone)]
pub struct AppState {
    pub config: Arc<Config>,
}

pub fn build_router(config: Config) -> Router {
    let state = AppState {
        config: Arc::new(config),
    };
    Router::new()
        .route("/health", get(handlers::health))
        .route("/query", post(handlers::query))
        .with_state(state)
}
