//! HTTP server binary for the bounded recursive-CTE backend.

use std::net::SocketAddr;
use std::process::ExitCode;

use recursive_cte_backend::api::router;
use recursive_cte_backend::config::{LogFormat, ServerConfig};
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> ExitCode {
    let config = match ServerConfig::from_env() {
        Ok(cfg) => cfg,
        Err(err) => {
            eprintln!("configuration error: {err}");
            return ExitCode::FAILURE;
        }
    };

    init_tracing(config.log_format);

    let host = match config.bind_host.parse::<std::net::IpAddr>() {
        Ok(ip) => ip,
        Err(err) => {
            eprintln!(
                "configuration error: CTE_HOST '{}' is not a valid IP address: {err}",
                config.bind_host
            );
            return ExitCode::FAILURE;
        }
    };
    let bind = SocketAddr::new(host, config.bind_port);
    tracing::info!(
        engine = "recursive-cte-backend",
        version = recursive_cte_backend::ENGINE_VERSION,
        bind = %bind,
        "starting server"
    );

    let listener = match tokio::net::TcpListener::bind(bind).await {
        Ok(l) => l,
        Err(err) => {
            tracing::error!(%bind, error = %err, "failed to bind listener");
            return ExitCode::FAILURE;
        }
    };

    let app = router(config);
    if let Err(err) = axum::serve(listener, app).await {
        tracing::error!(error = %err, "server terminated with error");
        return ExitCode::FAILURE;
    }
    ExitCode::SUCCESS
}

fn init_tracing(format: LogFormat) {
    let filter = EnvFilter::try_from_env("CTE_LOG").unwrap_or_else(|_| EnvFilter::new("info"));
    match format {
        LogFormat::Json => {
            tracing_subscriber::fmt()
                .json()
                .with_env_filter(filter)
                .with_target(false)
                .init();
        }
        LogFormat::Text => {
            tracing_subscriber::fmt()
                .with_env_filter(filter)
                .with_target(false)
                .init();
        }
    }
}
