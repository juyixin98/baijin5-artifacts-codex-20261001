//! End-to-end HTTP tests over a real TCP listener (no extra client deps).
//! Covers: health, successful multiset op, input error (400), run-id conflict
//! (409), resource exhaustion (422), fixture path escape denial, and the
//! run-events replay endpoint.

mod common;

use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::PathBuf;
use std::time::Duration;

use serde_json::Value;
use tempfile::tempdir;

fn fixture_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("fixtures")
}

struct Server {
    port: u16,
}

impl Server {
    fn start(fixture: PathBuf, spill: PathBuf) -> Server {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let port = listener.local_addr().unwrap().port();
        std::thread::spawn(move || {
            let rt = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .unwrap();
            rt.block_on(async move {
                let listener = tokio::net::TcpListener::from_std(listener).unwrap();
                let state = set_ops::service::AppState::new(fixture, spill);
                axum::serve(listener, set_ops::service::router(state))
                    .await
                    .unwrap();
            });
        });
        std::thread::sleep(Duration::from_millis(300));
        Server { port }
    }

    fn request(&self, method: &str, path: &str, body: Option<&str>) -> (u16, Value) {
        let mut stream = TcpStream::connect(("127.0.0.1", self.port)).unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(10)))
            .unwrap();
        let body = body.unwrap_or("");
        let req = format!(
            "{method} {path} HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n\
             Content-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        );
        stream.write_all(req.as_bytes()).unwrap();
        let mut raw = String::new();
        stream.read_to_string(&mut raw).unwrap();
        let mut split = raw.split("\r\n\r\n");
        let head = split.next().unwrap();
        let payload = split.next().unwrap_or("");
        let status: u16 = head
            .lines()
            .next()
            .and_then(|l| l.split_whitespace().nth(1))
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        let json = serde_json::from_str(payload).unwrap_or(Value::Null);
        (status, json)
    }
}

fn basic_request(op: &str, qualifier: &str, mode: &str) -> Value {
    serde_json::json!({
        "op": op,
        "qualifier": qualifier,
        "mode": mode,
        "schema": "id:bigint,label:text",
        "left": {"rows": [
            [1, "a"], [1, "a"], [2, null], [3, "12"]
        ], "batch_rows": 2},
        "right": {"rows": [
            [1, "a"], [2, null], [4, "x"]
        ]},
        "limits": {"memory_bytes": 32, "partition_fanout": 2}
    })
}

#[test]
fn health_ok() {
    let dir = tempdir().unwrap();
    let s = Server::start(fixture_root(), dir.path().join("spill"));
    let (status, body) = s.request("GET", "/health", None);
    assert_eq!(status, 200);
    assert_eq!(body["status"], "ok");
}

#[test]
fn query_success_envelope_and_correct_multiset() {
    let dir = tempdir().unwrap();
    let s = Server::start(fixture_root(), dir.path().join("spill"));
    let (status, body) = s.request(
        "POST",
        "/v1/query",
        Some(&basic_request("EXCEPT", "all", "auto").to_string()),
    );
    assert_eq!(status, 200, "body: {body}");
    assert_eq!(body["status"], "ok");
    assert!(body["run_id"].is_string());
    // L - R: (1,a) x1, (3,"12") x1
    let rows = body["rows"].as_array().unwrap();
    assert_eq!(rows.len(), 2);
    let mut sorted = rows.clone();
    sorted.sort_by_key(|r| r[0].as_i64().unwrap());
    assert_eq!(sorted[0][0], 1);
    assert_eq!(sorted[0][1], "a");
    assert_eq!(sorted[1][0], 3);
    assert_eq!(sorted[1][1], "12");
    assert!(
        body["stats"]["spills"].as_u64().unwrap() >= 1,
        "forced tiny budget spills"
    );
    assert!(body["replay"]["run_id"].as_str().is_some());
}

#[test]
fn bad_operator_is_input_error_400() {
    let dir = tempdir().unwrap();
    let s = Server::start(fixture_root(), dir.path().join("spill"));
    let mut req = basic_request("UNION", "distinct", "auto");
    req["op"] = "OUTER_JOIN".into();
    let (status, body) = s.request("POST", "/v1/query", Some(&req.to_string()));
    assert_eq!(status, 400);
    assert_eq!(body["error"]["kind"], "input");
    assert_eq!(body["error"]["code"], "invalid_request");
}

#[test]
fn type_mismatch_row_is_input_error_with_location() {
    let dir = tempdir().unwrap();
    let s = Server::start(fixture_root(), dir.path().join("spill"));
    let mut req = basic_request("UNION", "distinct", "in_memory");
    req["left"]["rows"][0][0] = "not-an-int".into();
    let (status, body) = s.request("POST", "/v1/query", Some(&req.to_string()));
    assert_eq!(status, 400);
    assert_eq!(body["error"]["kind"], "input");
    assert_eq!(body["error"]["code"], "parse_value");
    let ctx = body["error"]["context"]["location"].as_str().unwrap_or("");
    assert!(ctx.contains("row 1"), "context pinpoints row: {ctx}");
}

#[test]
fn resource_exhaustion_is_distinct_status_and_code() {
    let dir = tempdir().unwrap();
    let s = Server::start(fixture_root(), dir.path().join("spill"));
    let req = serde_json::json!({
        "op": "UNION", "qualifier": "all", "mode": "in_memory",
        "schema": "v:text",
        "left": {"rows": (0..40).map(|i| [Value::String(format!("v{i}"))]).map(|a| vec![a[0].clone()]).collect::<Vec<_>>()},
        "right": {"rows": []},
        "limits": {"memory_bytes": 64}
    });
    let (status, body) = s.request("POST", "/v1/query", Some(&req.to_string()));
    assert_eq!(status, 422);
    assert_eq!(body["error"]["kind"], "resource_exhausted");
    assert_eq!(body["error"]["code"], "memory_budget");
}

#[test]
fn count_overflow_rejected_with_422() {
    let dir = tempdir().unwrap();
    let s = Server::start(fixture_root(), dir.path().join("spill"));
    let mut req = basic_request("UNION", "all", "in_memory");
    req["limits"]["max_count"] = 1.into();
    let (status, body) = s.request("POST", "/v1/query", Some(&req.to_string()));
    assert_eq!(status, 422);
    assert_eq!(body["error"]["kind"], "resource_exhausted");
    assert_eq!(body["error"]["code"], "count_overflow");
}

#[test]
fn duplicate_run_id_conflicts_409_and_events_replay() {
    let dir = tempdir().unwrap();
    let s = Server::start(fixture_root(), dir.path().join("spill"));
    let mut req = basic_request("UNION", "distinct", "auto");
    req["run_id"] = "api-fixed-id".into();
    let (s1, b1) = s.request("POST", "/v1/query", Some(&req.to_string()));
    assert_eq!(s1, 200, "{b1}");
    let (s2, b2) = s.request("POST", "/v1/query", Some(&req.to_string()));
    assert_eq!(s2, 409);
    assert_eq!(b2["error"]["kind"], "state_conflict");
    assert_eq!(b2["error"]["code"], "run_id_conflict");

    let (s3, b3) = s.request("GET", "/v1/runs/api-fixed-id/events", None);
    assert_eq!(s3, 200);
    let events = b3["events"].as_array().unwrap();
    assert!(events.iter().any(|e| e["event"] == "verdict"));
    assert!(events.iter().any(|e| e["event"] == "query_start"));

    let (s4, b4) = s.request("GET", "/v1/runs/no-such-run/events", None);
    assert_eq!(s4, 409);
    assert_eq!(b4["error"]["kind"], "state_conflict");
    assert_eq!(b4["error"]["code"], "unknown_run");
}

#[test]
fn fixture_source_and_path_escape_denied() {
    let dir = tempdir().unwrap();
    let s = Server::start(fixture_root(), dir.path().join("spill"));
    let ok = serde_json::json!({
        "op": "INTERSECT", "qualifier": "all", "mode": "auto",
        "schema": "a:text,b:text",
        "left": {"fixture": "edge_left.csv"},
        "right": {"fixture": "edge_right.csv"}
    });
    let (status, body) = s.request("POST", "/v1/query", Some(&ok.to_string()));
    assert_eq!(status, 200, "{body}");
    assert!(body["stats"]["output_rows"].as_u64().unwrap() >= 5);

    let evil = serde_json::json!({
        "op": "UNION", "qualifier": "distinct", "mode": "in_memory",
        "schema": "a:text,b:text",
        "left": {"fixture": "../../../../etc/passwd"},
        "right": {"fixture": "edge_right.csv"}
    });
    let (status2, body2) = s.request("POST", "/v1/query", Some(&evil.to_string()));
    assert_eq!(status2, 400);
    assert_eq!(body2["error"]["kind"], "input");
    assert_eq!(body2["error"]["code"], "fixture_path_denied");
}
