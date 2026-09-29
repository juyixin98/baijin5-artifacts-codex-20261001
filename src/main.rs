//! HTTP server entry point. Binds the typed operator core to Axum; no join
//! logic lives here.

use std::net::SocketAddr;
use std::path::PathBuf;

use iejoin_range::api::{router, AppState};
use iejoin_range::replay::ReplayLogger;

#[tokio::main]
async fn main() {
    // Local-only defaults; everything is configurable via environment so no
    // address/secret is baked in.
    let host = std::env::var("IEJOIN_HOST").unwrap_or_else(|_| "127.0.0.1".to_string());
    let port: u16 = std::env::var("IEJOIN_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(8080);
    let session_capacity: usize = std::env::var("IEJOIN_SESSION_CAP")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(128);
    let replay_path: Option<PathBuf> = std::env::var("IEJOIN_REPLAY_LOG").ok().map(PathBuf::from);

    let replay = match replay_path {
        Some(path) => ReplayLogger::with_file(4096, &path).expect("open replay log"),
        None => ReplayLogger::memory(4096),
    };
    let state = AppState::new(session_capacity, replay);

    let addr: SocketAddr = format!("{host}:{port}")
        .parse()
        .expect("valid socket address");
    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .expect("bind address");
    eprintln!("iejoin-range listening on http://{addr}");
    axum::serve(listener, router(state))
        .await
        .expect("server runs");
}
