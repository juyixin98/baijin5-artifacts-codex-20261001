//! Service bootstrap: config from environment, tracing, HTTP server.

use std::{error::Error, net::SocketAddr, sync::Arc};

use cow_snap::{api, config::Config, engine::CowEngine};
use tokio::sync::Mutex;
use uuid::Uuid;

fn env_usize(key: &str, default: usize) -> Result<usize, Box<dyn Error>> {
    match std::env::var(key) {
        Ok(v) => Ok(v.parse().map_err(|e| format!("invalid {key}={v:?}: {e}"))?),
        Err(_) => Ok(default),
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    let mut config = Config::new(std::env::var("COW_SNAP_DATA_DIR").unwrap_or("./data".into()));
    config.page_size = env_usize("COW_SNAP_PAGE_SIZE", cow_snap::config::DEFAULT_PAGE_SIZE)?;
    config.logical_pages =
        env_usize("COW_SNAP_LOGICAL_PAGES", cow_snap::config::DEFAULT_LOGICAL_PAGES)?;
    config.capacity_pages =
        env_usize("COW_SNAP_CAPACITY_PAGES", cow_snap::config::DEFAULT_CAPACITY_PAGES)?;
    let port = env_usize("COW_SNAP_PORT", 8080)? as u16;

    let service_run_id = Uuid::new_v4().to_string();
    let engine = CowEngine::open(config)?;
    let state = Arc::new(api::AppState {
        engine: Mutex::new(engine),
        service_run_id: service_run_id.clone(),
    });

    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let listener = tokio::net::TcpListener::bind(addr).await?;
    tracing::info!(
        run_id = %service_run_id,
        addr = %addr,
        "cow-snap listening"
    );
    axum::serve(listener, api::router(state)).await?;
    Ok(())
}
