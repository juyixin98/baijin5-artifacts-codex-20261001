//! `groupagg-server` binary: load configuration, build the Axum router and
//! serve the grouped-aggregation validation/execution API.

use std::net::SocketAddr;
use std::path::PathBuf;

use groupagg::config::Settings;
use groupagg::server::{build_router, AppState};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    let config_path = std::env::var("GROUPAGG_CONFIG")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("config/groupagg.toml"));
    let settings = Settings::load(&config_path)?;
    let bind = SocketAddr::new(
        settings.host.parse().map_err(|e| {
            groupagg::Error::invalid_request(format!("invalid bind host {}: {e}", settings.host))
        })?,
        settings.port,
    );

    tracing::info!(
        host = %settings.host,
        port = settings.port,
        budget_bytes = settings.engine.memory_budget_bytes,
        spill = %settings.engine.spill_root.display(),
        "starting groupagg-server"
    );

    let state = AppState::new(settings.engine);
    let app = build_router(state).into_make_service_with_connect_info::<SocketAddr>();
    let listener = tokio::net::TcpListener::bind(bind).await?;
    tracing::info!("listening on http://{bind}");
    axum::serve(listener, app).await?;
    Ok(())
}
