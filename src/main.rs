//! Server binary: loads config, installs tracing, serves the codec API.

use rib::api::{router, AppState};
use rib::config::AppConfig;
use rib::encode::EncodeOptions;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    let config = match AppConfig::load() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("config error: {}", e);
            std::process::exit(2);
        }
    };
    let addr = format!("{}:{}", config.server.host, config.server.port);
    let state = AppState {
        budget: config.budget,
        encode_options: EncodeOptions::default(),
    };

    let listener = match tokio::net::TcpListener::bind(&addr).await {
        Ok(l) => l,
        Err(e) => {
            eprintln!("cannot bind {}: {}", addr, e);
            std::process::exit(2);
        }
    };
    tracing::info!(%addr, "rib-server listening");
    if let Err(e) = axum::serve(listener, router(state)).await {
        eprintln!("server error: {}", e);
        std::process::exit(1);
    }
}
