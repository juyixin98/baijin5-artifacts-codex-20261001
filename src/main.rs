use std::path::PathBuf;
use std::sync::Arc;

use pullq::service::{router, AppState};

const DEFAULT_PORT: u16 = 8173;
const DEFAULT_MEMORY_LIMIT: usize = 64 * 1024 * 1024;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "pullq=info,tower_http=info".into()),
        )
        .init();

    let port: u16 = std::env::var("PORT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(DEFAULT_PORT);
    let memory_limit: usize = std::env::var("MEMORY_LIMIT_BYTES")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(DEFAULT_MEMORY_LIMIT);
    let spill_root = std::env::var("SPILL_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| std::env::temp_dir().join("pullq-spill"));

    let state = Arc::new(AppState::new(memory_limit, spill_root.clone()));
    let app = router(state);

    let listener = tokio::net::TcpListener::bind(("127.0.0.1", port))
        .await
        .expect("bind listener");
    tracing::info!(port, memory_limit, spill_root = %spill_root.display(), "pullq listening");
    axum::serve(listener, app).await.expect("serve");
}
