//! Service entrypoint. Configuration is via environment:
//! `PORT` (default 8080), `RUST_LOG` (default `iblt_service=info,tower_http=info`).

use iblt_service::limits::Limits;
use iblt_service::service;
use tracing::info;
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| EnvFilter::new("iblt_service=info")),
        )
        .init();

    let port: u16 = std::env::var("PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(8080);

    let limits = Limits::default();
    info!(
        port,
        max_cells = limits.max_cells,
        max_keys = limits.max_keys,
        max_body_bytes = limits.max_body_bytes,
        "iblt-service listening"
    );

    let app = service::app(limits);
    let listener = tokio::net::TcpListener::bind(("0.0.0.0", port)).await?;
    axum::serve(listener, app).await?;
    Ok(())
}
