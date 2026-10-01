//! Black-box HTTP tests.
//!
//! The app is bound to a real OS listener on 127.0.0.1:0 and exercised with a
//! dependency-free HTTP/1.1 client over `std::net::TcpStream`. These tests
//! assert status codes, the categorised `error_kind` envelope and concrete row
//! sets — including that a malformed/unknown/type-bad request is reported as a
//! failure rather than an empty success.

use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::Arc;
use std::time::Duration;

use serde_json::{json, Value};
use tvindex::api::limited_router;
use tvindex::config::Config;
use tvindex::state::{AppState, TableStore};

struct Server {
    addr: String,
}

fn start_server(manifest: &str) -> Server {
    let mut config = Config::default();
    config.data.manifest = format!("fixtures/{manifest}").into();
    config.data.fixture_dir = "fixtures".into();
    config.server.host = "127.0.0.1".into();
    config.server.port = 0;
    let config = Arc::new(config);

    let store = TableStore::load(&config).expect("fixture loads");
    let state = AppState {
        store,
        config: config.clone(),
    };
    let app = limited_router(state, config.server.max_body_bytes);

    let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind");
    listener.set_nonblocking(true).expect("nonblocking");
    let addr = listener.local_addr().unwrap();

    std::thread::spawn(move || {
        let rt = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        rt.block_on(async move {
            let listener = tokio::net::TcpListener::from_std(listener).unwrap();
            axum::serve(listener, app).await.unwrap();
        });
    });

    // Wait for readiness with a cheap connect loop.
    let addr = format!("127.0.0.1:{}", addr.port());
    for _ in 0..50 {
        if std::net::TcpStream::connect_timeout(&addr.parse().unwrap(), Duration::from_millis(50))
            .is_ok()
        {
            break;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    Server { addr }
}

struct HttpResponse {
    status: u16,
    body: Value,
}

fn request(server: &Server, method: &str, path: &str, body: Option<&str>) -> HttpResponse {
    let mut stream = TcpStream::connect(&server.addr).expect("connect to server");
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let mut req = format!("{method} {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n");
    if let Some(b) = body {
        req.push_str(&format!(
            "Content-Type: application/json\r\nContent-Length: {}\r\n",
            b.len()
        ));
    }
    req.push_str("\r\n");
    if let Some(b) = body {
        req.push_str(b);
    }
    stream.write_all(req.as_bytes()).unwrap();
    let mut raw = String::new();
    stream.read_to_string(&mut raw).unwrap();
    let mut split = raw.splitn(2, "\r\n\r\n");
    let head = split.next().unwrap();
    let body_raw = split.next().unwrap_or("");
    let status: u16 = head
        .lines()
        .next()
        .unwrap()
        .split_whitespace()
        .nth(1)
        .unwrap()
        .parse()
        .unwrap();
    // Json responses carry Content-Length (hyper knows the body length); if a
    // chunked body ever appears, locating the first JSON object still parses
    // the complete object for these small single-object responses.
    let json_start = body_raw.find('{').expect("response has a JSON body");
    let body: Value = serde_json::from_str(&body_raw[json_start..]).expect("json body parses");
    HttpResponse { status, body }
}

#[test]
fn health_and_schema_report_versions() {
    let s = start_server("people.toml");
    let h = request(&s, "GET", "/healthz", None);
    assert_eq!(h.status, 200);
    assert_eq!(h.body["status"], "ok");

    let sc = request(&s, "GET", "/schema", None);
    assert_eq!(sc.status, 200);
    assert_eq!(sc.body["table"], "people");
    assert_eq!(sc.body["rows"], 7);
    assert_eq!(sc.body["live_rows"], 6, "row 6 deleted at v2");
    assert_eq!(sc.body["content_version"], 1, "deletes keep content at v1");
    assert_eq!(sc.body["head_version"], 2);
}

#[test]
fn query_returns_concrete_three_class_partition() {
    let s = start_server("people.toml");
    let body = json!({
        "where": {"op":"and","args":[
            {"op":"cmp","column":"age","cmp":">=","value":18},
            {"op":"cmp","column":"active","cmp":"=","value":true}
        ]}
    });
    let r = request(&s, "POST", "/query", Some(&body.to_string()));
    assert_eq!(r.status, 200, "{}", r.body);
    assert_eq!(r.body["success"], true);
    assert_eq!(r.body["selected_rows"], json!([0]));
    assert_eq!(r.body["counts"]["true"], 1);
    assert_eq!(r.body["counts"]["false"], 2);
    assert_eq!(r.body["counts"]["unknown"], 3);
    assert_eq!(r.body["universe"]["total"], 7);
    assert_eq!(r.body["universe"]["live"], 6);
    assert_eq!(r.body["universe"]["deleted_rows"], json!([6]));
    // Every row verdict is attributable and present exactly once.
    let rows = r.body["rows"].as_array().unwrap();
    assert_eq!(rows.len(), 6);
    assert!(rows
        .iter()
        .all(|r| r["row"].is_number() && r["verdict"].is_string()));
    assert!(r.body["run_id"].is_number());
}

#[test]
fn unknown_status_unknowns_are_not_success() {
    let s = start_server("people.toml");

    let cases: &[(&str, &str, u16, &str)] = &[
        // (body, path, status, error_kind)
        (
            r#"{"where":{"op":"cmp","column":"age","cmp":"=","value":null}}"#,
            "/query",
            400,
            "invalid_query",
        ),
        (
            r#"{"where":{"op":"cmp","column":"ghost","cmp":"=","value":1}}"#,
            "/query",
            404,
            "unknown_column",
        ),
        (
            r#"{"where":{"op":"cmp","column":"age","cmp":"=","value":"x"}}"#,
            "/query",
            400,
            "type_error",
        ),
        (
            r#"{"where":{"op":"and","args":[]}}"#,
            "/query",
            400,
            "invalid_query",
        ),
        (
            r#"{"where":{"op":"is_null","column":"age"},"as_of":50}"#,
            "/query",
            400,
            "invalid_query",
        ),
    ];
    for (body, path, status, kind) in cases {
        let r = request(&s, "POST", path, Some(body));
        assert_eq!(r.status, *status, "body={body} resp={}", r.body);
        assert_eq!(r.body["success"], false, "body={body}");
        assert_eq!(r.body["error_kind"], *kind, "body={body} resp={}", r.body);
        assert!(!r.body["message"].as_str().unwrap().is_empty());
    }
}

#[test]
fn missing_predicate_body_is_a_bad_request_not_empty_result() {
    let s = start_server("people.toml");
    let r = request(&s, "POST", "/query", Some("{}"));
    assert_eq!(r.status, 400);
    assert_eq!(r.body["success"], false);
    assert_eq!(r.body["error_kind"], "invalid_query");
}

#[test]
fn malformed_json_body_is_our_error_envelope_not_a_framework_page() {
    let s = start_server("people.toml");
    let r = request(&s, "POST", "/query", Some("{not json"));
    assert_eq!(r.status, 400);
    assert_eq!(r.body["success"], false);
    assert_eq!(r.body["error_kind"], "invalid_query");
    assert!(r.body["message"]
        .as_str()
        .unwrap()
        .contains("not valid query JSON"));
}

#[test]
fn edge67_tail_delete_served_over_http() {
    let s = start_server("edge67.toml");
    // NOT(id = 66) at head: row 66 deleted; must not be resurrected to FALSE.
    let body = json!({"as_of":2,"where":{"op":"not","arg":
        {"op":"cmp","column":"id","cmp":"=","value":66}}});
    let r = request(&s, "POST", "/query", Some(&body.to_string()));
    assert_eq!(r.status, 200, "{}", r.body);
    assert_eq!(r.body["universe"]["total"], 67);
    assert_eq!(r.body["universe"]["live"], 66);
    assert_eq!(r.body["universe"]["deleted_rows"], json!([66]));
    let false_rows = r.body["false_rows"].as_array().unwrap();
    assert!(
        !false_rows.iter().any(|v| v == 66),
        "deleted tail row leaked into FALSE: {}",
        r.body
    );

    // At v1 the tail row is live and matches id = 66.
    let body = json!({"as_of":1,"where":
        {"op":"cmp","column":"id","cmp":"=","value":66}});
    let r1 = request(&s, "POST", "/query", Some(&body.to_string()));
    assert_eq!(r1.body["selected_rows"], json!([66]));
    assert_eq!(r1.body["universe"]["live"], 67);
}
