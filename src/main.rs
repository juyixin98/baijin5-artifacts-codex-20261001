//! cdc-server: HTTP front-end for the content-defined chunking service.
//!
//! Usage: `cdc-server [--config PATH]`
//! Config precedence: defaults < file < env (CDC_LISTEN, CDC_MAX_BODY_BYTES,
//! CDC_MAX_CONCURRENT). Default config path: `config/default.toml` when it
//! exists.

use std::process::ExitCode;
use std::sync::Arc;

use cdc_service::api::{build_router, AppState};
use cdc_service::config::ServerConfig;
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> ExitCode {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")),
        )
        .init();

    let config_path = parse_config_arg().or_else(|| {
        std::env::var("CDC_CONFIG").ok().or_else(|| {
            std::path::Path::new("config/default.toml")
                .exists()
                .then(|| "config/default.toml".to_string())
        })
    });

    let cfg = match ServerConfig::load(config_path.as_deref()) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("configuration error: {e}");
            return ExitCode::FAILURE;
        }
    };

    let state = Arc::new(AppState::new(cfg.chunk, cfg.limits));
    let app = build_router(state);

    let listener = match tokio::net::TcpListener::bind(&cfg.listen).await {
        Ok(l) => l,
        Err(e) => {
            eprintln!("cannot bind {}: {e}", cfg.listen);
            return ExitCode::FAILURE;
        }
    };

    tracing::info!(
        listen = %cfg.listen,
        min_size = cfg.chunk.min_size,
        avg_bits = cfg.chunk.avg_bits,
        max_size = cfg.chunk.max_size,
        max_body_bytes = cfg.limits.max_body_bytes,
        max_concurrent = cfg.limits.max_concurrent,
        "cdc-server starting"
    );

    if let Err(e) = axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await
    {
        eprintln!("server error: {e}");
        return ExitCode::FAILURE;
    }
    ExitCode::SUCCESS
}

fn parse_config_arg() -> Option<String> {
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        if arg == "--config" {
            return args.next();
        }
        if let Some(path) = arg.strip_prefix("--config=") {
            return Some(path.to_string());
        }
    }
    None
}

async fn shutdown_signal() {
    let _ = tokio::signal::ctrl_c().await;
    tracing::info!("shutdown signal received");
}
