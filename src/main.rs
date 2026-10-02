use std::sync::{Arc, Mutex};

use cow_snapshot_service::api;
use cow_snapshot_service::config::ServiceConfig;
use cow_snapshot_service::engine::Engine;

fn main() {
    let cfg = match ServiceConfig::from_env() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("config error: {e}");
            std::process::exit(2);
        }
    };
    let bind = cfg.bind.clone();
    let engine = match Engine::init_or_open(&cfg) {
        Ok(e) => e,
        Err(e) => {
            eprintln!("startup error: {e}");
            std::process::exit(1);
        }
    };
    let run_id = engine.run_id().to_string();
    let stats = engine.stats();
    println!("cow-snapshot-service starting");
    println!("  run_id:    {run_id}");
    println!("  data_dir:  {}", cfg.data_dir.display());
    println!("  page_size: {}", stats.page_size);
    println!("  page_count: {}", stats.page_count);
    println!("  capacity:  {}", stats.capacity);
    println!("  objects:   {}", stats.objects_used);
    println!("  quarantined: {}", stats.quarantined);
    println!("  listen:    http://{bind}");

    let shared = Arc::new(Mutex::new(engine));
    let app = api::router(shared);

    let rt = tokio::runtime::Runtime::new().expect("tokio runtime");
    rt.block_on(async move {
        let listener = tokio::net::TcpListener::bind(&bind)
            .await
            .unwrap_or_else(|e| panic!("bind {bind}: {e}"));
        axum::serve(listener, app).await.expect("serve");
    });
}
