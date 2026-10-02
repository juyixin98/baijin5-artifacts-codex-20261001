//! `pq-server` launcher. All routing lives in `pull_query::http` so integration
//! tests can drive the same router in-process.

use std::net::SocketAddr;

#[tokio::main]
async fn main() {
    let port: u16 = std::env::var("PQ_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(8080);
    let addr: SocketAddr = ([127, 0, 0, 1], port).into();

    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .expect("bind listener");
    eprintln!("pull-query server listening on http://{addr}");
    axum::serve(listener, pull_query::http::app())
        .await
        .expect("server run");
}
