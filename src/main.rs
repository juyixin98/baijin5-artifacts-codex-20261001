use rbp_column::config::AppConfig;
use rbp_column::server::{build_router, AppState};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info,tower_http=info".into()),
        )
        .init();

    let cfg = AppConfig::load()?;
    let addr = format!("{}:{}", cfg.server.host, cfg.server.port);
    tracing::info!(addr, "starting rbp-column service");

    let listener = tokio::net::TcpListener::bind(&addr).await?;
    let app = build_router(AppState::new(cfg));
    axum::serve(listener, app).await?;
    Ok(())
}
