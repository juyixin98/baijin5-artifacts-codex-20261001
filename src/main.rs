use procdiff::api::{router, AppState};
use procdiff::config::Config;
use procdiff::engine::Engine;
use procdiff::store::Store;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "procdiff=info,tower_http=info".into()),
        )
        .init();

    let cfg_path = std::env::var("PROCDIFF_CONFIG")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("config/default.toml"));
    let cfg = Config::load_or_default(&cfg_path)?;
    tracing::info!(listen = %cfg.listen, data_dir = %cfg.data_dir.display(), "starting procdiff");

    let store = Store::new(cfg.data_dir.clone());
    let state = store.load()?.unwrap_or_default();
    if state.last_seq.is_some() {
        tracing::info!(last_seq = ?state.last_seq, "resumed persisted sampling state");
    }
    let engine = Engine::from_state(cfg.clone(), state);
    let app = router(Arc::new(AppState {
        engine: Mutex::new(engine),
        store,
    }));

    let runtime = tokio::runtime::Runtime::new()?;
    runtime.block_on(async move {
        let listener = tokio::net::TcpListener::bind(&cfg.listen).await?;
        tracing::info!(addr = %cfg.listen, "diagnostic API ready");
        axum::serve(listener, app).await?;
        Ok::<(), std::io::Error>(())
    })?;
    Ok(())
}
