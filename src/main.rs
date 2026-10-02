//! 服务入口：加载配置，初始化存储，启动 Axum 诊断服务。

use iosched_compare::api::{AppState, SERVICE_VERSION, router};
use iosched_compare::config::ServerConfig;
use iosched_compare::state::RunStore;
use std::sync::Arc;

#[tokio::main]
async fn main() {
    let config_path = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "config/server.json".to_string());
    let (config, warning) = ServerConfig::load(&config_path);
    if let Some(w) = warning {
        eprintln!("[config] {w}");
    }

    let store = match RunStore::new(&config.data_dir) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("[fatal] cannot open data dir '{}': {e}", config.data_dir);
            std::process::exit(1);
        }
    };

    let state = Arc::new(AppState {
        config: config.clone(),
        store,
    });
    let app = router(state);

    let listener = match tokio::net::TcpListener::bind(&config.listen_addr).await {
        Ok(l) => l,
        Err(e) => {
            eprintln!("[fatal] cannot bind {}: {e}", config.listen_addr);
            std::process::exit(1);
        }
    };
    println!(
        "iosched-compare v{SERVICE_VERSION} listening on {}",
        config.listen_addr
    );
    println!("time model: synthetic mechanical seek model (NOT an SSD measurement)");
    if let Err(e) = axum::serve(listener, app).await {
        eprintln!("[fatal] server error: {e}");
        std::process::exit(1);
    }
}
