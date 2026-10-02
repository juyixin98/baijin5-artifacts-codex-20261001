//! Binary entry point: serves the Axum application.
//!
//! Configuration via environment variables (all optional):
//! - `SETOPS_BIND`   bind address (default `127.0.0.1:8080`)
//! - `SETOPS_DATA`   scratch/run root directory (default `./.setops-data`)
//! - `SETOPS_MEMORY_BYTES`, `SETOPS_BUFFER_BYTES`, `SETOPS_TABLE_BYTES`,
//!   `SETOPS_SPILL_BYTES`, `SETOPS_SPILL_FILES`, `SETOPS_OUTPUT_ROWS`:
//!   override the default budgets.

use setops::resource::Budget;
use setops::service;

#[derive(Debug)]
struct Config {
    bind: String,
    data: String,
    budget: Budget,
}

fn env_or(key: &str, default: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| default.to_string())
}

fn env_parse<T: std::str::FromStr>(key: &str, default: T) -> T
where
    <T as std::str::FromStr>::Err: std::fmt::Debug,
{
    std::env::var(key)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}

fn load_config() -> Config {
    let mut budget = Budget::default();
    budget.memory_bytes = env_parse("SETOPS_MEMORY_BYTES", budget.memory_bytes);
    budget.partition_buffer_bytes = env_parse("SETOPS_BUFFER_BYTES", budget.partition_buffer_bytes);
    budget.partition_table_bytes = env_parse("SETOPS_TABLE_BYTES", budget.partition_table_bytes);
    budget.spill_bytes = env_parse("SETOPS_SPILL_BYTES", budget.spill_bytes);
    budget.spill_files = env_parse("SETOPS_SPILL_FILES", budget.spill_files);
    budget.output_buffer_rows = env_parse("SETOPS_OUTPUT_ROWS", budget.output_buffer_rows);
    Config {
        bind: env_or("SETOPS_BIND", "127.0.0.1:8080"),
        data: env_or("SETOPS_DATA", "./.setops-data"),
        budget,
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let config = load_config();
    config.budget.validate()?;

    let root = std::path::PathBuf::from(&config.data);
    let runs_root = root.join("runs");
    std::fs::create_dir_all(&runs_root)?;
    setops::runlog::init_run_root(runs_root.clone())
        .map_err(|e| std::io::Error::other(e.to_string()))?;

    let listener = tokio::net::TcpListener::bind(&config.bind).await?;
    eprintln!(
        "setops listening on {} (data root {}, memory {}B, buffer {}B, table {}B, spill {}B)",
        config.bind,
        config.data,
        config.budget.memory_bytes,
        config.budget.partition_buffer_bytes,
        config.budget.partition_table_bytes,
        config.budget.spill_bytes
    );

    let app = service::app(runs_root, config.budget);
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await?;
    Ok(())
}

async fn shutdown_signal() {
    let ctrl_c = async {
        tokio::signal::ctrl_c()
            .await
            .expect("install Ctrl-C handler");
    };

    #[cfg(unix)]
    let terminate = async {
        let mut sig = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("signal");
        sig.recv().await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {},
        _ = terminate => {},
    }
}
