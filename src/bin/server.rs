//! `decorr-server` — HTTP entrypoint.

use std::net::SocketAddr;

use decorr::config::Config;
use decorr::http;
use tracing_subscriber::{fmt, EnvFilter};

#[tokio::main]
async fn main() {
    let config = Config::load(None).unwrap_or_else(|err| {
        eprintln!("fatal: invalid configuration: {err}");
        std::process::exit(2);
    });

    init_tracing(&config);

    let bind: SocketAddr = config
        .bind_address()
        .parse()
        .unwrap_or_else(|err| panic!("invalid bind address `{}`: {err}", config.bind_address()));

    let service = decorr::build_app_state_with(config.clone());
    let app = http::router(service);

    tracing::info!(%bind, engine_version = decorr::service::engine_version(), "decorr-server starting");

    let listener = tokio::net::TcpListener::bind(bind)
        .await
        .unwrap_or_else(|err| {
            tracing::error!(%bind, error = %err, "failed to bind listener");
            std::process::exit(1);
        });

    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await
        .unwrap_or_else(|err| {
            tracing::error!(error = %err, "server error");
            std::process::exit(1);
        });
}

fn init_tracing(config: &Config) {
    let filter =
        EnvFilter::try_new(&config.server.log_level).unwrap_or_else(|_| EnvFilter::new("info"));
    let subscriber = fmt().with_env_filter(filter);
    if config.server.json_logs {
        subscriber.json().init();
    } else {
        subscriber.init();
    }
}

async fn shutdown_signal() {
    let ctrl_c = async {
        tokio::signal::ctrl_c()
            .await
            .expect("failed to install Ctrl-C handler");
    };

    #[cfg(unix)]
    let terminate = async {
        let mut sig = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("SIGTERM handler");
        sig.recv().await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {},
        _ = terminate => {},
    }
    tracing::info!("shutdown signal received");
}
