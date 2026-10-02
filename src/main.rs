use std::sync::{Arc, Mutex};

use ioq_runtime::adapter::scripted::ScriptedAdapter;
use ioq_runtime::api::{build_router, spawn_pump, Runtime, Shared};
use ioq_runtime::config::RuntimeConfig;
use ioq_runtime::engine::Engine;
use ioq_runtime::journal::FileJournal;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();

    let config_path = std::env::var("IOQ_CONFIG")
        .ok()
        .or_else(|| std::env::args().nth(1))
        .unwrap_or_else(|| "config/runtime.toml".to_string());
    let cfg = RuntimeConfig::load(&config_path)?;

    let engine = Engine::new(
        cfg.engine_config(),
        Box::new(FileJournal::create(&cfg.journal_path)?),
    );
    let adapter = ScriptedAdapter::demo_server(cfg.default_io_delay_ms);
    let shared: Shared = Arc::new(Mutex::new(Runtime::new(engine, Box::new(adapter))));

    let pump = spawn_pump(shared.clone(), cfg.pump_interval_ms);
    let app = build_router(shared);

    let listener = tokio::net::TcpListener::bind(&cfg.bind).await?;
    tracing::info!(bind = %cfg.bind, journal = %cfg.journal_path.display(), "ioq-runtime listening");
    axum::serve(listener, app).await?;
    pump.abort();
    Ok(())
}
