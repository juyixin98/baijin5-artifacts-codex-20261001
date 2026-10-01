//! HTTP server entry point.
//!
//! `cargo run --bin server`
//!
//! Loads layered configuration, installs tracing (logs carry run ids, table
//! versions and per-step 3VL cardinalities), and serves the Axum router.

use std::path::PathBuf;
use std::sync::{Arc, RwLock};

use tracing::info;
use tribool_index::api::{router, AppState};
use tribool_index::config::Config;
use tribool_index::state::Catalog;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let config_path = std::env::args().nth(1).map(PathBuf::from).or_else(|| {
        let p = PathBuf::from("config/default.toml");
        if p.exists() {
            Some(p)
        } else {
            None
        }
    });

    let config = Config::load(config_path.as_deref())?;

    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_new(&config.log.level)
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    let bind = format!("{}:{}", config.server.host, config.server.port);
    info!(bind = %bind, log_level = %config.log.level, service = "tribool-index", version = env!("CARGO_PKG_VERSION"), "starting server");

    let state = AppState {
        catalog: Arc::new(RwLock::new(Catalog::new())),
        config: Arc::new(config.clone()),
    };

    let listener = tokio::net::TcpListener::bind(&bind).await?;
    info!(bind = %bind, "listening");
    axum::serve(listener, router(state))
        .with_graceful_shutdown(shutdown_signal())
        .await?;
    info!("server shut down gracefully");
    Ok(())
}

async fn shutdown_signal() {
    let ctrl_c = async {
        tokio::signal::ctrl_c()
            .await
            .expect("install Ctrl-C handler");
    };

    #[cfg(unix)]
    let terminate = async {
        if let Ok(mut sig) =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
        {
            sig.recv().await;
        } else {
            std::future::pending::<()>().await;
        }
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {},
        _ = terminate => {},
    }
}
