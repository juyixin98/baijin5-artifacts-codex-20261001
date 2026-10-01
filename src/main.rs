//! Leapfrog Triejoin HTTP server entry point.
use std::net::SocketAddr;

use leapfrog_triejoin::{build_default_catalog, router, AppState, ServerConfig};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info,leapfrog_triejoin=debug".into()),
        )
        .init();

    let config = ServerConfig::from_env();
    let catalog = build_default_catalog();
    let addr = SocketAddr::new(
        config
            .bind_host
            .parse()
            .expect("LFJ_BIND_HOST must be a valid IP address"),
        config.bind_port,
    );

    let state = AppState::new(config.clone(), catalog);
    let app = router(state);

    let listener = tokio::net::TcpListener::bind(addr).await?;
    tracing::info!(%addr, max_relations = config.max_relations, "LFJ server listening");
    axum::serve(listener, app).await?;
    Ok(())
}
