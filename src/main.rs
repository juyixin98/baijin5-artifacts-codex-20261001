//! Server entrypoint: loads config, opens the store, serves the API.

use merge_checker::config::Config;
use merge_checker::routes::{self, AppState};
use merge_checker::store::RunStore;
use std::path::PathBuf;
use std::sync::Arc;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    let config_path = std::env::var("MERGE_CHECKER_CONFIG")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("config/default.toml"));
    let config = Config::load(&config_path).unwrap_or_else(|e| {
        eprintln!("failed to load config {}: {e}", config_path.display());
        std::process::exit(2);
    });
    let store = RunStore::open(&config.store_path).unwrap_or_else(|e| {
        eprintln!("failed to open store {}: {e}", config.store_path.display());
        std::process::exit(2);
    });

    let listen = config.listen.clone();
    let state = AppState {
        config: Arc::new(config),
        store: Arc::new(store),
    };
    let app = routes::router(state);

    let listener = tokio::net::TcpListener::bind(&listen)
        .await
        .unwrap_or_else(|e| {
            eprintln!("failed to bind {listen}: {e}");
            std::process::exit(2);
        });
    tracing::info!(
        listen = %listen,
        version = merge_checker::model::TOOL_VERSION,
        "merge-checker-server listening"
    );
    axum::serve(listener, app).await.expect("server error");
}
