use std::sync::Arc;

use stackstats::config::Config;
use stackstats::store::RunStore;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    let config_path = std::env::args().nth(1).unwrap_or_else(|| "config/default.toml".to_string());
    let config = Config::load(&config_path)?;
    let store = Arc::new(RunStore::open(config.data_dir.clone(), config.limits.clone())?);
    let app = stackstats::api::router(store);
    let listener = tokio::net::TcpListener::bind(&config.bind).await?;
    tracing::info!(bind = %config.bind, data_dir = %config.data_dir.display(), "stackstats listening");
    axum::serve(listener, app).await?;
    Ok(())
}
