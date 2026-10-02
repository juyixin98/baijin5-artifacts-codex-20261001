//! End-to-end HTTP evidence: boot the REAL Axum router on an ephemeral
//! loopback port and speak HTTP/1.1 over a raw TCP socket (no external client
//! dependency). Covers validation-only, execution, failure categories,
//! version/health, and the request-body resource limit.

mod common;

use common::CaseBuilder;
use recursive_cte_backend::api::router;
use recursive_cte_backend::config::ServerConfig;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;

struct TestServer {
    addr: std::net::SocketAddr,
    shutdown: tokio::sync::oneshot::Sender<()>,
}

async fn spawn_server(config: ServerConfig) -> TestServer {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let app = router(config);
    let (tx, rx) = tokio::sync::oneshot::channel::<()>();
    tokio::spawn(async move {
        let server = axum::serve(listener, app).with_graceful_shutdown(async {
            let _ = rx.await;
        });
        server.await.unwrap();
    });
    TestServer { addr, shutdown: tx }
}

async fn http_json(
    addr: std::net::SocketAddr,
    method: &str,
    path: &str,
    body: Option<&str>,
) -> (u16, serde_json::Value) {
    let mut stream = TcpStream::connect(addr).await.unwrap();
    let payload = body.unwrap_or("");
    let request = if method == "POST" {
        format!(
            "{method} {path} HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{payload}",
            payload.len()
        )
    } else {
        format!("{method} {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
    };
    stream.write_all(request.as_bytes()).await.unwrap();

    let mut raw = Vec::new();
    stream.read_to_end(&mut raw).await.unwrap();

    let text = String::from_utf8_lossy(&raw);
    let split = text.find("\r\n\r\n").expect("header/body separator");
    let head = &text[..split];
    let mut body_bytes = raw[split + 4..].to_vec();

    let status: u16 = head
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|s| s.parse().ok())
        .unwrap();

    // Honor Transfer-Encoding: chunked if present; otherwise the body is raw.
    if head
        .to_ascii_lowercase()
        .contains("transfer-encoding: chunked")
    {
        body_bytes = dechunk(&body_bytes);
    }

    let json = serde_json::from_slice(&body_bytes).unwrap_or_else(|_| {
        panic!(
            "non-JSON body (status {status}): {}",
            String::from_utf8_lossy(&body_bytes)
        )
    });
    (status, json)
}

fn dechunk(input: &[u8]) -> Vec<u8> {
    let mut out = Vec::new();
    let mut i = 0;
    while i < input.len() {
        let line_end = input[i..]
            .windows(2)
            .position(|w| w == b"\r\n")
            .map(|p| i + p);
        let line_end = match line_end {
            Some(p) => p,
            None => break,
        };
        let size_str = std::str::from_utf8(&input[i..line_end]).unwrap().trim();
        let size = usize::from_str_radix(size_str, 16).unwrap_or(0);
        let chunk_start = line_end + 2;
        if size == 0 {
            break;
        }
        out.extend_from_slice(&input[chunk_start..chunk_start + size]);
        i = chunk_start + size + 2;
    }
    out
}

#[tokio::test]
async fn version_and_health_endpoints_report_engine_version() {
    let server = spawn_server(ServerConfig::default()).await;
    let (status, body) = http_json(server.addr, "GET", "/v1/version", None).await;
    assert_eq!(status, 200);
    assert_eq!(
        body["version"],
        serde_json::json!(recursive_cte_backend::ENGINE_VERSION)
    );

    let (status, body) = http_json(server.addr, "GET", "/healthz", None).await;
    assert_eq!(status, 200);
    assert_eq!(body["status"], "ok");
    let _ = server.shutdown.send(());
}

#[tokio::test]
async fn validate_endpoint_accepts_good_plan_without_running() {
    let server = spawn_server(ServerConfig::default()).await;
    let req = CaseBuilder::new("http-validate", &[(1, 2)]).build();
    let raw = serde_json::to_string(&req).unwrap();
    let (status, body) = http_json(server.addr, "POST", "/v1/recursive/validate", Some(&raw)).await;
    assert_eq!(status, 200);
    assert_eq!(body["valid"], serde_json::json!(true));
    assert!(body["run_id"].as_str().unwrap().starts_with("run-"));
    // Validation never produces result rows.
    assert!(body.get("output").is_none());
    let _ = server.shutdown.send(());
}

#[tokio::test]
async fn execute_endpoint_runs_self_loop_with_marker_and_tied_log() {
    let server = spawn_server(ServerConfig::default()).await;
    let req = CaseBuilder::new("http-self-loop", &[(1, 2), (2, 2)])
        .limits(8, 100)
        .build();
    let raw = serde_json::to_string(&req).unwrap();
    let (status, body) = http_json(server.addr, "POST", "/v1/recursive/execute", Some(&raw)).await;
    assert_eq!(status, 200, "body: {body}");
    assert_eq!(body["status"], "complete");
    assert_eq!(body["stats"]["output_rows"], 3);
    assert_eq!(body["stats"]["cycles_marked"], 1);

    // The marker row is present in the JSON output.
    let last = body["output"]["rows"][2].clone();
    assert_eq!(last, serde_json::json!([2, 2, [1, 2, 2], true]));

    // Logs are tied to the response run id and carry version/decision text.
    let run_id = body["run_id"].as_str().unwrap();
    assert_eq!(
        body["engine_version"],
        serde_json::json!(recursive_cte_backend::ENGINE_VERSION)
    );
    let log = body["log"].as_array().unwrap();
    assert!(!log.is_empty());
    for entry in log {
        assert_eq!(entry["run_id"], serde_json::json!(run_id));
        assert!(!entry["detail"].as_str().unwrap().is_empty());
    }
    assert!(log.iter().any(|e| e["step"] == "cycle_marked"));
    let _ = server.shutdown.send(());
}

#[tokio::test]
async fn invalid_plan_returns_400_with_concrete_category_not_success() {
    let server = spawn_server(ServerConfig::default()).await;
    let mut req = CaseBuilder::new("http-bad", &[(1, 2)]).build();
    req.recursive_term.on.clear();
    let raw = serde_json::to_string(&req).unwrap();
    let (status, body) = http_json(server.addr, "POST", "/v1/recursive/execute", Some(&raw)).await;
    assert_eq!(status, 400);
    assert_eq!(body["status"], "failed");
    assert_eq!(body["failure_category"], "invalid_plan");
    assert!(body["message"].as_str().unwrap().contains("equi-join"));
    assert!(body["run_id"].as_str().unwrap().starts_with("run-"));
    let _ = server.shutdown.send(());
}

#[tokio::test]
async fn malformed_json_is_invalid_data_not_a_server_crash() {
    let server = spawn_server(ServerConfig::default()).await;
    let (status, body) = http_json(
        server.addr,
        "POST",
        "/v1/recursive/execute",
        Some("{not json"),
    )
    .await;
    assert_eq!(status, 422, "body: {body}");
    assert_eq!(body["status"], "failed");
    assert_eq!(body["failure_category"], "invalid_data");
    let _ = server.shutdown.send(());
}

#[tokio::test]
async fn incomplete_run_is_reported_as_200_with_explicit_status() {
    let server = spawn_server(ServerConfig::default()).await;
    let req = CaseBuilder::new("http-incomplete", &[(1, 2), (2, 3)])
        .limits(1, 100)
        .build();
    let raw = serde_json::to_string(&req).unwrap();
    let (status, body) = http_json(server.addr, "POST", "/v1/recursive/execute", Some(&raw)).await;
    // A bounded-but-incomplete answer is still a delivered result.
    assert_eq!(status, 200);
    assert_eq!(body["status"], "incomplete");
    assert_eq!(body["incomplete_reason"], "max_depth");
    let _ = server.shutdown.send(());
}

#[tokio::test]
async fn body_larger_than_configured_limit_is_rejected() {
    let config = ServerConfig {
        max_request_bytes: 128,
        ..Default::default()
    };
    let server = spawn_server(config).await;
    let req = CaseBuilder::new("http-too-big", &[(1, 2)]).build();
    let raw = serde_json::to_string(&req).unwrap();
    assert!(raw.len() > 128, "test premise: request exceeds the cap");
    let mut stream = TcpStream::connect(server.addr).await.unwrap();
    let request = format!(
        "POST /v1/recursive/execute HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{raw}",
        raw.len()
    );
    stream.write_all(request.as_bytes()).await.unwrap();
    let mut response = Vec::new();
    stream.read_to_end(&mut response).await.unwrap();
    let head = String::from_utf8_lossy(&response);
    assert!(
        head.starts_with("HTTP/1.1 413"),
        "expected 413 payload too large, got: {head}"
    );
    let _ = server.shutdown.send(());
}
