//! Service entry point: `blksched-server`.
//!
//! Env overrides: `BLKSCHED_CONFIG` (config path), `BLKSCHED_DATA_DIR`,
//! `BLKSCHED_BIND`.

use blksched::api::{router, AppState};
use blksched::config::Config;
use blksched::state::RunStore;
use std::path::Path;
use std::sync::Arc;

#[tokio::main]
async fn main() {
    let config_path = std::env::var("BLKSCHED_CONFIG")
        .unwrap_or_else(|_| "config/default.json".to_string());
    let mut cfg = Config::load(Path::new(&config_path));
    if let Ok(dir) = std::env::var("BLKSCHED_DATA_DIR") {
        cfg.data_dir = dir;
    }
    if let Ok(bind) = std::env::var("BLKSCHED_BIND") {
        cfg.bind_addr = bind;
    }

    let store = RunStore::new(&cfg.data_dir).unwrap_or_else(|e| {
        eprintln!("fatal: cannot open data dir {}: {e}", cfg.data_dir);
        std::process::exit(1);
    });
    let state = Arc::new(AppState { store, cfg });
    let app = router(state.clone());

    let listener = tokio::net::TcpListener::bind(&state.cfg.bind_addr)
        .await
        .unwrap_or_else(|e| {
            eprintln!("fatal: cannot bind {}: {e}", state.cfg.bind_addr);
            std::process::exit(1);
        });
    eprintln!(
        "blksched-server {} listening on http://{} (data dir: {})",
        env!("CARGO_PKG_VERSION"),
        state.cfg.bind_addr,
        state.cfg.data_dir
    );
    axum::serve(listener, app).await.expect("server error");
}
