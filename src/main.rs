use cdc_service::api;
use cdc_service::config::ServiceConfig;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    let config_path = std::env::args().nth(1);
    let config = match ServiceConfig::load(config_path.as_deref()) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("configuration error: {e}");
            std::process::exit(2);
        }
    };

    let listen = config.listen.clone();
    let app = api::router(config);
    let listener = tokio::net::TcpListener::bind(&listen)
        .await
        .unwrap_or_else(|e| {
            eprintln!("cannot bind {listen}: {e}");
            std::process::exit(1);
        });
    tracing::info!(listen = %listen, "cdc-service listening");
    axum::serve(listener, app).await.unwrap();
}
