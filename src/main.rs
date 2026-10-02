//! merge-checkd: server entrypoint. Wires config, telemetry and the API.

use merge_check::api::build_router;
use merge_check::config::AppConfig;
use std::path::PathBuf;
use std::process::ExitCode;
use tracing_subscriber::EnvFilter;

fn main() -> ExitCode {
    let config_path = std::env::var("MERGE_CHECK_CONFIG")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("config/default.toml"));

    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")),
        )
        .with_target(true)
        .init();

    let config = match AppConfig::load(&config_path) {
        Ok(c) => c,
        Err(e) => {
            tracing::error!(error = %e, path = %config_path.display(), "failed to load config");
            return ExitCode::FAILURE;
        }
    };

    let bind = config.bind.clone();
    tracing::info!(
        bind = %bind,
        state_dir = %config.state_dir.display(),
        layer_roots = ?config.layer_roots,
        version = merge_check::model::ENGINE_VERSION,
        "starting merge-checkd"
    );

    let runtime = match tokio::runtime::Runtime::new() {
        Ok(rt) => rt,
        Err(e) => {
            tracing::error!(error = %e, "failed to start tokio runtime");
            return ExitCode::FAILURE;
        }
    };

    runtime.block_on(async move {
        let listener = match tokio::net::TcpListener::bind(&bind).await {
            Ok(l) => l,
            Err(e) => {
                tracing::error!(error = %e, bind = %bind, "failed to bind");
                return ExitCode::FAILURE;
            }
        };
        if let Err(e) = axum::serve(listener, build_router(config)).await {
            tracing::error!(error = %e, "server error");
            return ExitCode::FAILURE;
        }
        ExitCode::SUCCESS
    })
}
