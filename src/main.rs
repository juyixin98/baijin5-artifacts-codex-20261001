//! pctl-server entry point.

use pctl::{build_router, Config};
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // RUST_LOG controls verbosity; default to INFO on the pctl targets.
    let filter =
        EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info,pctl=debug"));
    tracing::subscriber::set_global_default(
        tracing_subscriber::fmt()
            .with_env_filter(filter)
            .with_target(true)
            .json()
            .finish(),
    )?;

    // Allow `--config path` or default to config/pctl.toml relative to CWD.
    let cfg_path = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "config/pctl.toml".to_string());
    let config = Config::load(cfg_path)?;
    config.ensure_spill_dir()?;

    tracing::info!(
        bind = %config.bind_addr,
        budget_mib = config.memory_budget_bytes / 1024 / 1024,
        spill_dir = %config.spill_dir.display(),
        "starting pctl-server"
    );

    let bind_addr = config.bind_addr.clone();
    let router = build_router(config);
    let listener = tokio::net::TcpListener::bind(&bind_addr).await?;
    tracing::info!("listening on http://{}", listener.local_addr()?);

    axum::serve(listener, router).await?;
    Ok(())
}
