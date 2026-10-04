//! Shared helpers for integration tests.
// Each integration-test binary compiles this module separately and may not use
// every helper.
#![allow(dead_code)]

use std::sync::Arc;

use psi_dh::server::{build_router, AppState};
use psi_dh::store::Store;

/// Spawn a server on an ephemeral port with an in-memory store.
/// Returns the base URL. The server lives for the rest of the test process.
pub async fn spawn_server() -> String {
    let state = Arc::new(AppState {
        store: Store::in_memory().expect("in-memory store"),
        max_set_size: 10_000,
    });
    let app = build_router(state, 16 * 1024 * 1024);
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind ephemeral port");
    let addr = listener.local_addr().expect("local addr");
    tokio::spawn(async move {
        axum::serve(listener, app).await.expect("serve");
    });
    format!("http://{addr}")
}

pub fn elements(prefix: &str, range: std::ops::Range<u32>) -> Vec<Vec<u8>> {
    range
        .map(|i| format!("{prefix}-{i:04}").into_bytes())
        .collect()
}

/// Run a blocking closure (e.g. the reqwest-blocking-based `Client`) off the
/// async runtime, so its internal runtime is created and dropped on a
/// blocking thread instead of inside the test's async context.
pub async fn blocking<F, T>(f: F) -> T
where
    F: FnOnce() -> T + Send + 'static,
    T: Send + 'static,
{
    tokio::task::spawn_blocking(f).await.expect("blocking task")
}
