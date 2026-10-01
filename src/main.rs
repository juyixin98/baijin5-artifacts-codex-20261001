//! Server entry point: load config/state, wire tracing, serve the axum router.

use std::net::SocketAddr;

use collate_agg::api;
use collate_agg::state::{AppConfig, AppState};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .json()
        .init();

    let config_path =
        std::env::var("COLLATE_CONFIG").unwrap_or_else(|_| "config/collate_agg.toml".to_string());
    let config = AppConfig::load_file(&config_path)?.apply_env();
    let addr: SocketAddr = config
        .bind_addr
        .parse()
        .map_err(|e| format!("invalid bind_addr {}: {e}", config.bind_addr))?;

    tracing::info!(
        bind = %addr,
        default_rule = %config.default_rule_version,
        oracle_crosscheck = config.oracle_crosscheck,
        redact_sensitive = config.redact_sensitive,
        "starting collate_agg"
    );

    let listener = tokio::net::TcpListener::bind(addr).await?;
    let app = api::router(AppState::new(config));
    axum::serve(listener, app).await?;
    Ok(())
}
