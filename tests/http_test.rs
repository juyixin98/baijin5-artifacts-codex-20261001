//! Real HTTP end-to-end tests against the Axum router on an ephemeral port.
//!
//! These exercise the transport: request-id propagation, classified status
//! codes, and the explainable response envelope over an actual TCP socket.

mod common;

use decorrelate_svc::config::AppConfig;
use decorrelate_svc::server::router;
use decorrelate_svc::state::AppState;
use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;

use common::*;

async fn serve() -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap().to_string();
    let state = AppState::new(AppConfig::default());
    let app = router(state);
    tokio::spawn(async move {
        let _ = axum::serve(listener, app).await;
    });
    addr
}

async fn http_post(
    addr: &str,
    path: &str,
    body: &str,
    req_id: Option<&str>,
) -> (u16, String, String) {
    let mut stream = TcpStream::connect(addr).await.unwrap();
    let mut req = format!(
        "POST {path} HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {}\r\n",
        body.len()
    );
    if let Some(id) = req_id {
        req.push_str(&format!("x-request-id: {id}\r\n"));
    }
    req.push_str("Connection: close\r\n\r\n");
    req.push_str(body);
    stream.write_all(req.as_bytes()).await.unwrap();

    let mut raw = String::new();
    stream.read_to_string(&mut raw).await.unwrap();
    let split = raw.split_once("\r\n\r\n").unwrap();
    let status: u16 = split.0.lines().next().unwrap()["HTTP/1.1 ".len().."HTTP/1.1 ".len() + 3]
        .parse()
        .unwrap();
    let rid_header = split
        .0
        .lines()
        .find(|l| l.to_lowercase().starts_with("x-request-id:"))
        .map(|l| l.split(':').nth(1).unwrap().trim().to_string())
        .unwrap_or_default();
    (status, rid_header, split.1.to_string())
}

async fn http_get(addr: &str, path: &str) -> (u16, String) {
    let mut stream = TcpStream::connect(addr).await.unwrap();
    let req = format!("GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n");
    stream.write_all(req.as_bytes()).await.unwrap();
    let mut raw = String::new();
    stream.read_to_string(&mut raw).await.unwrap();
    let split = raw.split_once("\r\n\r\n").unwrap();
    let status: u16 = split.0.lines().next().unwrap()["HTTP/1.1 ".len().."HTTP/1.1 ".len() + 3]
        .parse()
        .unwrap();
    (status, split.1.to_string())
}

#[tokio::test]
async fn health_and_version_endpoints_report_identity() {
    let addr = serve().await;
    let (code, body) = http_get(&addr, "/health").await;
    assert_eq!(code, 200);
    let v: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(v["status"], "ok");
    assert!(v["version"].is_string());

    let (code, body) = http_get(&addr, "/version").await;
    assert_eq!(code, 200);
    let v: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(v["arrow2"], "0.18.0");
}

#[tokio::test]
async fn request_id_is_echoed_in_header_and_body() {
    let addr = serve().await;
    let body = serde_json::to_string(&request(json!([exists_term("lines", "l", false)]))).unwrap();
    let (code, rid, resp) = http_post(&addr, "/query", &body, Some("abc-123")).await;
    assert_eq!(code, 200);
    assert_eq!(rid, "abc-123");
    let v: Value = serde_json::from_str(&resp).unwrap();
    assert_eq!(v["request_id"], "abc-123");
    assert_eq!(v["location"], "local-synthetic");
    assert_eq!(v["status"], "ok");
}

#[tokio::test]
async fn generated_request_id_when_header_absent() {
    let addr = serve().await;
    let body = serde_json::to_string(&request(json!([exists_term("lines", "l", false)]))).unwrap();
    let (code, rid, resp) = http_post(&addr, "/query", &body, None).await;
    assert_eq!(code, 200);
    assert!(!rid.is_empty());
    let v: Value = serde_json::from_str(&resp).unwrap();
    assert_eq!(v["request_id"], rid);
}

#[tokio::test]
async fn not_in_returns_400_with_classified_error() {
    let addr = serve().await;
    let body = serde_json::to_string(&request(json!([in_term(
        "lines",
        "l",
        "code",
        col("want"),
        true
    )])))
    .unwrap();
    let (code, _, resp) = http_post(&addr, "/query", &body, Some("reject-1")).await;
    assert_eq!(code, 400);
    let v: Value = serde_json::from_str(&resp).unwrap();
    assert_eq!(v["status"], "error");
    assert_eq!(v["errors"][0]["kind"], "unsupported_form");
    assert_eq!(v["request_id"], "reject-1");
}

#[tokio::test]
async fn malformed_json_returns_400_invalid_request() {
    let addr = serve().await;
    let (code, _, resp) = http_post(&addr, "/query", "{ not json", Some("bad-json")).await;
    assert_eq!(code, 400);
    let v: Value = serde_json::from_str(&resp).unwrap();
    assert_eq!(v["errors"][0]["kind"], "invalid_request");
    assert_eq!(v["errors"][0]["at_step"], "parse_request");
}

#[tokio::test]
async fn scalar_multiple_rows_returns_422() {
    let addr = serve().await;
    let scalar = json!({
        "kind": "scalar_sub",
        "outer": col("want"),
        "op": "eq",
        "sub": sub("scalar_src", "s",
            json!([corr("o", "cust", "s", "cust")]),
            json!({"project": qcol("s", "code")}))
    });
    let body = serde_json::to_string(&request(json!([scalar]))).unwrap();
    let (code, _, resp) = http_post(&addr, "/query", &body, Some("multi")).await;
    assert_eq!(code, 422);
    let v: Value = serde_json::from_str(&resp).unwrap();
    assert_eq!(v["errors"][0]["kind"], "scalar_multiple_rows");
    assert_eq!(v["errors"][0]["engine"], "naive+rewrite");
}
