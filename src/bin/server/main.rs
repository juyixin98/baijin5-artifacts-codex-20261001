//! HTTP server binary for the Leapfrog Triejoin backend.
//!
//! Loads configuration from the environment, installs the bundled synthetic
//! fixtures, installs tracing diagnostics and serves the Axum router with
//! graceful shutdown.

use std::sync::Arc;

use leapfrog_triejoin::build_router;
use leapfrog_triejoin::config::Config;
use leapfrog_triejoin::state::{AppState, Catalog};
use tokio::net::TcpListener;
use tracing_subscriber::{fmt, EnvFilter};

mod fixtures;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")),
        )
        .json()
        .init();

    let config = Config::from_env()?;
    tracing::info!(?config, "starting leapfrog-triejoin server");

    let mut catalog = Catalog::new();
    let fixture_count = fixtures::load_bundled(&mut catalog)?;
    tracing::info!(relations = fixture_count, "bundled fixtures loaded");

    let state = Arc::new(AppState::from_parts(config.clone(), catalog));

    let app = build_router(state.clone());
    let listener = TcpListener::bind(&config.bind_addr).await?;
    tracing::info!(addr = %config.bind_addr, "listening");

    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await?;
    tracing::info!("server shut down");
    Ok(())
}

async fn shutdown_signal() {
    let ctrl_c = async {
        tokio::signal::ctrl_c()
            .await
            .expect("failed to install Ctrl+C handler");
    };

    #[cfg(unix)]
    let terminate = async {
        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("install SIGTERM handler")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {},
        _ = terminate => {},
    }
    tracing::info!("shutdown signal received");
}
