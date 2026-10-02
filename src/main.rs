use std::path::PathBuf;
use std::sync::Arc;

use proc_diff::config::Config;
use proc_diff::diag::{self, AppState};
use proc_diff::store::Store;

#[tokio::main]
async fn main() -> Result<(), String> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "proc_diff=info,tower_http=info".into()),
        )
        .init();

    let config_path = std::env::var("PROC_DIFF_CONFIG")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("config/default.toml"));
    let config = Config::load(&config_path)?;

    let store = Store::open(&config.data_dir).map_err(|e| format!("open store: {e}"))?;
    let (engine, outcomes) = store
        .replay(config.engine_config())
        .map_err(|e| format!("replay journal: {e}"))?;
    if !outcomes.is_empty() {
        tracing::info!(replayed = outcomes.len(), "replayed snapshot journal");
    }

    let state = Arc::new(AppState {
        engine: std::sync::Mutex::new(engine),
        store,
    });
    let app = diag::router(state);

    let listener = tokio::net::TcpListener::bind(&config.bind_addr)
        .await
        .map_err(|e| format!("bind {}: {e}", config.bind_addr))?;
    tracing::info!(addr = %config.bind_addr, "proc-diff listening");
    axum::serve(listener, app).await.map_err(|e| format!("serve: {e}"))
}
