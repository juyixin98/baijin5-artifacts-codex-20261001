//! HTTP server binary for decorrelate-svc.

use std::net::SocketAddr;

use decorrelate_svc::config::AppConfig;
use decorrelate_svc::server::router;
use decorrelate_svc::state::AppState;
use tracing_subscriber::{fmt, EnvFilter};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let config = AppConfig::from_env();
    let filter = EnvFilter::try_new(&config.log_filter)
        .or_else(|_| EnvFilter::try_new("info"))
        .unwrap();
    fmt().with_env_filter(filter).init();

    let addr: SocketAddr = config.bind_addr.parse()?;
    let state = AppState::new(config.clone());
    let app = router(state);

    tracing::info!(
        bind = %addr,
        version = env!("CARGO_PKG_VERSION"),
        "decorrelate-svc starting"
    );

    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}
