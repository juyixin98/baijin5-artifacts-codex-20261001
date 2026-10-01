//! Binary entry point: load configuration, wire logging, serve HTTP.

use recursive_cte::{config::Config, server, state};
use tracing_subscriber::{fmt, EnvFilter};

#[tokio::main]
async fn main() {
    let filter = EnvFilter::try_from_env("RCE_LOG").unwrap_or_else(|_| EnvFilter::new("info"));
    fmt()
        .with_env_filter(filter)
        .with_target(true)
        .json()
        .init();

    let config = match Config::from_env() {
        Ok(cfg) => cfg,
        Err(err) => {
            eprintln!("configuration error: {err}");
            std::process::exit(2);
        }
    };

    let bind_addr = config.bind_addr.clone();
    tracing::info!(
        version = state::ENGINE_VERSION,
        bind = %bind_addr,
        default_order = ?config.default_traversal,
        max_depth = config.max_depth,
        max_rows = config.max_rows,
        "starting recursive-cte backend"
    );

    let app = server::router(config);
    let listener = match tokio::net::TcpListener::bind(&bind_addr).await {
        Ok(l) => l,
        Err(err) => {
            eprintln!("failed to bind {bind_addr}: {err}");
            std::process::exit(1);
        }
    };
    if let Err(err) = axum::serve(listener, app).await {
        eprintln!("server error: {err}");
        std::process::exit(1);
    }
}
