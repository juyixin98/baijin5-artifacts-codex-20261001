//! Server binary: serves the diagnostic HTTP interface over an
//! `FsStore`-backed model.
//!
//! Usage: `mmap-model [config.toml]` (env: MMAP_MODEL_CONFIG,
//! MMAP_MODEL_BIND, MMAP_MODEL_STORAGE_DIR, RUST_LOG).

use mmap_model::config::ServerConfig;
use mmap_model::diag;
use mmap_model::model::Vm;
use mmap_model::store::FsStore;
use mmap_model::telemetry;
use std::sync::{Arc, Mutex};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let cfg_path = std::env::args()
        .nth(1)
        .or_else(|| std::env::var("MMAP_MODEL_CONFIG").ok());
    let cfg = ServerConfig::load(cfg_path.as_deref())?;

    telemetry::init_tracing();
    let run_id = diag::run_id();

    std::fs::create_dir_all(&cfg.storage_dir)?;
    let store = FsStore::new(&cfg.storage_dir);
    let vm = Vm::new(cfg.model.clone(), store);
    let app = diag::router(Arc::new(Mutex::new(vm)));

    let listener = tokio::net::TcpListener::bind(&cfg.bind).await?;
    tracing::info!(
        run_id = %run_id,
        version = telemetry::crate_version(),
        bind = %cfg.bind,
        storage_dir = %cfg.storage_dir.display(),
        page_size = cfg.model.page_size,
        "mmap-model server listening"
    );
    axum::serve(listener, app).await?;
    Ok(())
}
