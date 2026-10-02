//! HTTP API integration tests: the router runs in-process (tower oneshot),
//! the model is driven deterministically via `Runtime::tick_at`.

use std::sync::{Arc, Mutex};

use axum::body::{Body, Bytes};
use axum::http::{Request, StatusCode};
use ioq_runtime::adapter::scripted::ScriptedAdapter;
use ioq_runtime::api::{build_router, Runtime, Shared};
use ioq_runtime::engine::{Engine, EngineConfig};
use ioq_runtime::journal::MemJournal;
use tower::ServiceExt;

fn test_cfg() -> EngineConfig {
    EngineConfig {
        queue_capacity: 4,
        buffer_capacity: 4,
        timeout_ms: 5_000,
        snapshot_every: 4,
    }
}

fn app() -> (axum::Router, Shared) {
    let engine = Engine::new(test_cfg(), Box::new(MemJournal::new()));
    let adapter = ScriptedAdapter::demo_server(0);
    let shared: Shared = Arc::new(Mutex::new(Runtime::new(engine, Box::new(adapter))));
    (build_router(shared.clone()), shared)
}

async fn request(
    app: &axum::Router,
    method: &str,
    uri: &str,
    body: Option<serde_json::Value>,
) -> (StatusCode, Bytes) {
    let mut builder = Request::builder().method(method).uri(uri);
    let body = match body {
        Some(json) => {
            builder = builder.header("content-type", "application/json");
            Body::from(serde_json::to_vec(&json).unwrap())
        }
        None => Body::empty(),
    };
    let response = app
        .clone()
        .oneshot(builder.body(body).unwrap())
        .await
        .unwrap();
    let status = response.status();
    let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
        .await
        .unwrap();
    (status, bytes)
}

fn json(bytes: &Bytes) -> serde_json::Value {
    serde_json::from_slice(bytes).expect("json body")
}

async fn open_conn(app: &axum::Router) -> (u64, u64) {
    let (status, body) = request(app, "POST", "/connections", Some(serde_json::json!({}))).await;
    assert_eq!(status, StatusCode::OK);
    let body = json(&body);
    (body["conn_id"].as_u64().unwrap(), body["generation"].as_u64().unwrap())
}

#[tokio::test]
async fn open_submit_complete_flow() {
    let (app, rt) = app();
    let (conn, generation) = open_conn(&app).await;

    let (status, body) = request(
        &app,
        "POST",
        &format!("/connections/{conn}/submissions"),
        Some(serde_json::json!({
            "generation": generation,
            "op": {"kind": "read", "path": "/data/file.bin"},
            "request_id": "req-1"
        })),
    )
    .await;
    assert_eq!(status, StatusCode::CREATED);
    let token = json(&body)["user_data"].clone();
    let sid = token["submission"].as_u64().unwrap();

    rt.lock().unwrap().tick_at(0); // dispatch + immediate completion

    let (status, body) = request(&app, "GET", &format!("/submissions/{sid}"), None).await;
    assert_eq!(status, StatusCode::OK);
    let view = json(&body);
    assert_eq!(view["phase"], "final");
    assert_eq!(view["final_record"]["outcome"]["type"], "success");
    assert_eq!(view["final_record"]["outcome"]["bytes"], 512);
}

#[tokio::test]
async fn queue_full_returns_429_with_reason() {
    let (app, _rt) = app();
    let (conn, generation) = open_conn(&app).await;
    // capacity is 4 and nothing is pumped, so the 5th submit overflows.
    for i in 0..4 {
        let (status, _) = request(
            &app,
            "POST",
            &format!("/connections/{conn}/submissions"),
            Some(serde_json::json!({
                "generation": generation,
                "op": {"kind": "read", "path": format!("/data/{i}")}
            })),
        )
        .await;
        assert_eq!(status, StatusCode::CREATED);
    }
    let (status, body) = request(
        &app,
        "POST",
        &format!("/connections/{conn}/submissions"),
        Some(serde_json::json!({
            "generation": generation,
            "op": {"kind": "read", "path": "/data/overflow"}
        })),
    )
    .await;
    assert_eq!(status, StatusCode::TOO_MANY_REQUESTS);
    let body = json(&body);
    assert_eq!(body["error"]["code"], "queue_full");
    assert!(body["error"]["reason"]
        .as_str()
        .unwrap()
        .contains("backpressure"));
}

#[tokio::test]
async fn stale_generation_is_rejected_with_409() {
    let (app, _rt) = app();
    let (conn, generation) = open_conn(&app).await;
    let (status, _) = request(
        &app,
        "POST",
        &format!("/connections/{conn}/close"),
        Some(serde_json::json!({"generation": generation})),
    )
    .await;
    assert_eq!(status, StatusCode::OK); // nothing pending: closed at once

    let (_, new_generation) = open_conn(&app).await; // reuses the slot
    assert!(new_generation > generation);

    let (status, body) = request(
        &app,
        "POST",
        &format!("/connections/{conn}/submissions"),
        Some(serde_json::json!({
            "generation": generation, // stale
            "op": {"kind": "read", "path": "/data/x"}
        })),
    )
    .await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(json(&body)["error"]["code"], "stale_generation");
}

#[tokio::test]
async fn cancel_forwarded_path_and_final_record_is_authoritative() {
    // Dedicated runtime with a non-zero demo delay so the cancel lands
    // while the IO is in flight.
    let engine = Engine::new(test_cfg(), Box::new(MemJournal::new()));
    let adapter = ScriptedAdapter::demo_server(10); // flaky-cancel: 30ms
    let shared: Shared = Arc::new(Mutex::new(Runtime::new(engine, Box::new(adapter))));
    let app = build_router(shared.clone());

    let (conn, generation) = open_conn(&app).await;
    let (status, body) = request(
        &app,
        "POST",
        &format!("/connections/{conn}/submissions"),
        Some(serde_json::json!({
            "generation": generation,
            "op": {"kind": "read", "path": "demo:flaky-cancel"}
        })),
    )
    .await;
    assert_eq!(status, StatusCode::CREATED);
    let sid = json(&body)["user_data"]["submission"].as_u64().unwrap();

    shared.lock().unwrap().tick_at(0); // dispatch only

    let (status, body) = request(
        &app,
        "POST",
        &format!("/submissions/{sid}/cancel"),
        Some(serde_json::json!({"conn": conn, "generation": generation})),
    )
    .await;
    assert_eq!(status, StatusCode::ACCEPTED);
    let body = json(&body);
    assert_eq!(body["accepted"], "forwarded");
    assert!(body["note"].as_str().unwrap().contains("may still complete"));

    shared.lock().unwrap().tick_at(1); // forward the cancel
    shared.lock().unwrap().tick_at(40); // device completes anyway

    let (status, body) = request(&app, "GET", &format!("/submissions/{sid}"), None).await;
    assert_eq!(status, StatusCode::OK);
    let view = json(&body);
    // Cancel was accepted, yet the authoritative record is a success.
    assert_eq!(view["final_record"]["outcome"]["type"], "success");
    assert_eq!(view["final_record"]["cancel_requested"], true);
}

#[tokio::test]
async fn cancel_unknown_submission_is_404() {
    let (app, _rt) = app();
    let (conn, generation) = open_conn(&app).await;
    let (status, body) = request(
        &app,
        "POST",
        "/submissions/4242/cancel",
        Some(serde_json::json!({"conn": conn, "generation": generation})),
    )
    .await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(json(&body)["error"]["code"], "unknown_submission");
}

#[tokio::test]
async fn diagnostics_explain_decisions_and_redact_paths() {
    let (app, rt) = app();
    let (conn, generation) = open_conn(&app).await;
    let secret_path = "/home/alice/secret.txt";
    let (status, _) = request(
        &app,
        "POST",
        &format!("/connections/{conn}/submissions"),
        Some(serde_json::json!({
            "generation": generation,
            "op": {"kind": "read", "path": secret_path},
            "request_id": "req-redact"
        })),
    )
    .await;
    assert_eq!(status, StatusCode::CREATED);
    rt.lock().unwrap().tick_at(0);

    let (status, body) = request(&app, "GET", "/diag/events?since=0", None).await;
    assert_eq!(status, StatusCode::OK);
    let raw = String::from_utf8(body.to_vec()).unwrap();
    // Sensitive data must never appear in diagnostics.
    assert!(!raw.contains("alice"), "path leaked into diagnostics: {raw}");
    assert!(!raw.contains(secret_path));
    assert!(raw.contains("<path#"), "redacted marker missing: {raw}");

    let events = json(&body)["events"].as_array().unwrap().clone();
    let accepted = events
        .iter()
        .find(|e| e["kind"] == "submission_accepted")
        .expect("acceptance event");
    assert_eq!(accepted["decision"], "accept");
    assert_eq!(accepted["request_id"], "req-redact");
    assert!(accepted["reason"].as_str().unwrap().contains("queued"));
}

#[tokio::test]
async fn close_drains_then_closes() {
    let engine = Engine::new(test_cfg(), Box::new(MemJournal::new()));
    let adapter = ScriptedAdapter::demo_server(10); // demo:slow -> 100ms
    let shared: Shared = Arc::new(Mutex::new(Runtime::new(engine, Box::new(adapter))));
    let app = build_router(shared.clone());

    let (conn, generation) = open_conn(&app).await;
    let (status, _) = request(
        &app,
        "POST",
        &format!("/connections/{conn}/submissions"),
        Some(serde_json::json!({
            "generation": generation,
            "op": {"kind": "read", "path": "demo:slow"}
        })),
    )
    .await;
    assert_eq!(status, StatusCode::CREATED);
    shared.lock().unwrap().tick_at(0); // dispatch

    let (status, body) = request(
        &app,
        "POST",
        &format!("/connections/{conn}/close"),
        Some(serde_json::json!({"generation": generation})),
    )
    .await;
    assert_eq!(status, StatusCode::ACCEPTED);
    assert_eq!(json(&body)["lifecycle"], "draining");

    shared.lock().unwrap().tick_at(150); // completion arrives

    let (status, body) = request(
        &app,
        "GET",
        &format!("/connections/{conn}?generation={generation}"),
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(json(&body)["lifecycle"], "closed");
}
