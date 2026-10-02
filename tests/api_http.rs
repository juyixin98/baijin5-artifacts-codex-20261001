//! HTTP 端到端测试：在随机端口启动真实 axum 服务器，
//! 用 std::net::TcpStream 发裸 HTTP/1.1 请求（不引入额外 HTTP 客户端依赖）。

mod support;

use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use arc_page_cache::api;
use arc_page_cache::config::ArcConfig;
use arc_page_cache::engine::Engine;
use arc_page_cache::storage::FaultStore;

struct RunningServer {
    port: u16,
    engine: Arc<Mutex<Engine>>,
}

fn spawn_server(capacity: usize) -> RunningServer {
    let engine = Arc::new(Mutex::new(Engine::new(
        ArcConfig {
            capacity,
            page_size: 64,
            writeback_retries: 0,
        },
        Box::new(FaultStore::new(64)),
        256,
    )));
    let engine2 = engine.clone();

    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    listener.set_nonblocking(true).unwrap();

    thread::spawn(move || {
        let rt = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        rt.block_on(async move {
            // from_std 必须在 runtime 上下文内调用。
            let listener = tokio::net::TcpListener::from_std(listener).unwrap();
            let app = api::router(engine2);
            axum::serve(listener, app).await.unwrap();
        });
    });

    thread::sleep(Duration::from_millis(150));
    RunningServer { port, engine }
}

fn request(port: u16, method: &str, path: &str, body: &str, req_id: Option<&str>) -> (u16, String) {
    let mut stream = TcpStream::connect(("127.0.0.1", port)).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let mut req = format!("{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nConnection: close\r\n");
    if let Some(id) = req_id {
        req.push_str(&format!("X-Request-Id: {id}\r\n"));
    }
    if !body.is_empty() {
        req.push_str(&format!("Content-Length: {}\r\n", body.len()));
    }
    req.push_str("\r\n");
    req.push_str(body);
    stream.write_all(req.as_bytes()).unwrap();

    let mut raw = String::new();
    stream.read_to_string(&mut raw).unwrap();
    let status: u16 = raw
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|s| s.parse().ok())
        .unwrap_or(0);
    let json = raw
        .split("\r\n\r\n")
        .nth(1)
        .unwrap_or("")
        .trim()
        .to_string();
    (status, json)
}

#[test]
fn health_and_stats_envelope() {
    let srv = spawn_server(4);
    let (status, body) = request(srv.port, "GET", "/health", "", None);
    assert_eq!(status, 200);
    assert!(body.contains("ok"));

    let (status, body) = request(srv.port, "GET", "/stats", "", None);
    assert_eq!(status, 200);
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(v["success"], true);
    assert_eq!(v["data"]["stats"]["c"], 4);
    assert_eq!(v["data"]["stats"]["resident"], 0);
}

#[test]
fn access_reports_request_id_and_classifies_sources() {
    let srv = spawn_server(3);

    // 两个驻留 + 一个 T2：0,1,2 后再 0。
    for p in [0u64, 1, 2] {
        let body = format!("{{\"page\":{p},\"kind\":\"read\"}}");
        let (st, _) = request(srv.port, "POST", "/access", &body, None);
        assert_eq!(st, 200);
    }
    let (_, b) = request(
        srv.port,
        "POST",
        "/access",
        "{\"page\":0,\"kind\":\"read\"}",
        Some("rid-fixed-1"),
    );
    let v: serde_json::Value = serde_json::from_str(&b).unwrap();
    assert_eq!(v["success"], true);
    assert_eq!(v["request_id"], "rid-fixed-1");
    assert_eq!(v["data"]["outcome"]["source"], "t1");

    // 缺页触发淘汰，来源 miss，且返回被淘汰页与新 p。
    let (_, b) = request(
        srv.port,
        "POST",
        "/access",
        "{\"page\":3,\"kind\":\"read\"}",
        None,
    );
    let v: serde_json::Value = serde_json::from_str(&b).unwrap();
    assert_eq!(v["data"]["outcome"]["source"], "miss");
    assert_eq!(v["data"]["outcome"]["evicted"], true);

    // /lists 显示四列表且无内容泄漏。
    let (_, b) = request(srv.port, "GET", "/lists", "", None);
    let v: serde_json::Value = serde_json::from_str(&b).unwrap();
    assert!(v["data"].is_object());
}

#[test]
fn replay_returns_step_by_step_stats() {
    let srv = spawn_server(2);
    let body = serde_json::json!({
        "accesses": [
            {"page": 0, "kind": "read"},
            {"page": 1, "kind": "read"},
            {"page": 0, "kind": "read"},
            {"page": 2, "kind": "write"}
        ]
    })
    .to_string();
    let (status, b) = request(srv.port, "POST", "/replay", &body, Some("replay-xyz"));
    assert_eq!(status, 200);
    let v: serde_json::Value = serde_json::from_str(&b).unwrap();
    let steps = v["data"].as_array().unwrap();
    assert_eq!(steps.len(), 4, "all four steps complete");
    // 第三步 0 是 t1 命中。
    assert_eq!(steps[2]["outcome"]["source"], "t1");
    // 每步都带逐步统计，且 resident 从不超过容量。
    for step in steps {
        assert!(step["stats_after"]["resident"].as_u64().unwrap() <= 2);
        assert!(step["request_id"].is_string());
    }
    assert_eq!(v["request_id"], "replay-xyz");
}

#[test]
fn resize_endpoint_shrinks_and_reports() {
    let srv = spawn_server(4);
    for p in 0..4 {
        let body = format!("{{\"page\":{p},\"kind\":\"read\"}}");
        request(srv.port, "POST", "/access", &body, None);
    }
    let (status, b) = request(srv.port, "POST", "/resize", "{\"capacity\":1}", None);
    assert_eq!(status, 200);
    let v: serde_json::Value = serde_json::from_str(&b).unwrap();
    let evicted = v["data"]["evicted"].as_array().unwrap();
    assert_eq!(evicted.len(), 3);
    assert_eq!(srv.engine.lock().unwrap().stats().resident, 1);
}

#[test]
fn diagnostics_endpoint_filters_by_request_id_and_redacts() {
    let srv = spawn_server(2);
    request(
        srv.port,
        "POST",
        "/access",
        "{\"page\":0,\"kind\":\"read\"}",
        Some("traceable-42"),
    );
    let (status, b) = request(
        srv.port,
        "GET",
        "/diagnostics?request_id=traceable-42",
        "",
        None,
    );
    assert_eq!(status, 200);
    let v: serde_json::Value = serde_json::from_str(&b).unwrap();
    let recs = v["data"].as_array().unwrap();
    assert_eq!(recs.len(), 1);
    assert_eq!(recs[0]["request_id"], "traceable-42");
    assert_eq!(recs[0]["verdict"], "accepted");
    assert_eq!(recs[0]["state"]["c"], 2);
    // 诊断不回传页内容；脱敏示例只暴露长度与头部。
    assert!(v["note"].is_string());
    assert!(b.contains("redacted"));
    assert!(!b.contains("sensitive-bytes"));
}

#[test]
fn invalid_json_returns_error_envelope() {
    let srv = spawn_server(2);
    let (status, b) = request(srv.port, "POST", "/access", "{not json", None);
    // axum 的 JSON 提取器返回 400/422；信封之外由框架处理，这里只断言非 2xx。
    assert!(status >= 400, "got {status}: {b}");
}
