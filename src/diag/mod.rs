//! Diagnostic HTTP interface (Axum). Exposes the whole model — files,
//! mappings, reads/writes, sync, truncate, unmap, state inspection and
//! stats — so behaviour can be driven and verified end to end.

mod dto;
mod handlers;

use crate::model::Vm;
use crate::store::BackingStore;
use axum::routing::{delete, get, post};
use axum::Router;
use std::sync::{Arc, Mutex, OnceLock};

static RUN_ID: OnceLock<String> = OnceLock::new();

/// Process-wide run identity, stamped into /version and log lines.
pub fn run_id() -> String {
    RUN_ID.get_or_init(crate::telemetry::run_id).clone()
}

pub fn router<S: BackingStore + 'static>(vm: Arc<Mutex<Vm<S>>>) -> Router {
    Router::new()
        .route("/healthz", get(handlers::healthz))
        .route("/version", get(handlers::version::<S>))
        .route("/stats", get(handlers::stats::<S>))
        .route("/files", post(handlers::create_file::<S>))
        .route("/files/open", post(handlers::open_file::<S>))
        .route("/files/state", get(handlers::file_state::<S>))
        .route("/files/content", get(handlers::file_content::<S>))
        .route("/files/write", post(handlers::file_write::<S>))
        .route("/files/truncate", post(handlers::truncate_file::<S>))
        .route("/mappings", post(handlers::create_mapping::<S>))
        .route("/mappings/{id}/state", get(handlers::mapping_state::<S>))
        .route("/mappings/{id}/read", post(handlers::mapping_read::<S>))
        .route("/mappings/{id}/write", post(handlers::mapping_write::<S>))
        .route("/mappings/{id}/sync", post(handlers::mapping_sync::<S>))
        .route("/mappings/{id}", delete(handlers::mapping_unmap::<S>))
        .with_state(vm)
}
