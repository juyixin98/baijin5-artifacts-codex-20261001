//! HTTP-level tests: the Axum router is exercised in-process via
//! `tower::ServiceExt::oneshot`, verifying status codes, the structured
//! error body contract, and base64 payload handling end to end.

mod common;

use base64::Engine as _;
use common::*;
use cow_snapshot_service::api;
use cow_snapshot_service::engine::Engine;
use http_body_util::BodyExt;
use std::sync::{Arc, Mutex};
use tower::ServiceExt;

fn make_app(test_name: &str, page_size: usize, page_count: usize, capacity: usize) -> axum::Router {
    let cfg = test_cfg(test_dir(test_name), page_size, page_count, capacity);
    let engine = Engine::init_or_open(&cfg).unwrap();
    api::router(Arc::new(Mutex::new(engine)))
}

async fn call(
    app: &axum::Router,
    method: &str,
    uri: &str,
    body: Option<serde_json::Value>,
) -> (u16, serde_json::Value) {
    let mut builder = axum::http::Request::builder().method(method).uri(uri);
    if body.is_some() {
        builder = builder.header("content-type", "application/json");
    }
    let req = builder
        .body(axum::body::Body::from(
            body.map(|b| b.to_string()).unwrap_or_default(),
        ))
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    let status = resp.status().as_u16();
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    let json = if bytes.is_empty() {
        serde_json::Value::Null
    } else {
        serde_json::from_slice(&bytes).unwrap()
    };
    (status, json)
}

fn b64(data: &[u8]) -> String {
    base64::engine::general_purpose::STANDARD.encode(data)
}

#[tokio::test]
async fn http_end_to_end() {
    let app = make_app("http_end_to_end", 64, 8, 64);

    let (status, body) = call(&app, "GET", "/health", None).await;
    assert_eq!(status, 200);
    assert!(body["run_id"].as_str().unwrap().starts_with("run-"));

    // Write "hello" at page 0 offset 0.
    let (status, body) = call(
        &app,
        "POST",
        "/live/writes",
        Some(serde_json::json!({ "writes": [{ "page": 0, "offset": 0, "data_b64": b64(b"hello") }] })),
    )
    .await;
    assert_eq!(status, 200, "{body}");
    assert_eq!(body["pages_copied"], 1);

    let (status, body) = call(&app, "GET", "/live/pages/0", None).await;
    assert_eq!(status, 200);
    let data = base64::engine::general_purpose::STANDARD
        .decode(body["data_b64"].as_str().unwrap())
        .unwrap();
    assert_eq!(&data[..5], b"hello");
    assert_eq!(data.len(), 64);

    // Fork, overwrite, snapshot keeps the old content.
    let (status, body) = call(
        &app,
        "POST",
        "/snapshots",
        Some(serde_json::json!({ "name": "s1" })),
    )
    .await;
    assert_eq!(status, 200, "{body}");
    let snap_id = body["id"].as_u64().unwrap();

    let (status, _) = call(
        &app,
        "POST",
        "/live/writes",
        Some(serde_json::json!({ "writes": [{ "page": 0, "offset": 0, "data_b64": b64(b"WORLD") }] })),
    )
    .await;
    assert_eq!(status, 200);

    let (status, body) = call(&app, "GET", &format!("/snapshots/{snap_id}/pages/0"), None).await;
    assert_eq!(status, 200);
    let data = base64::engine::general_purpose::STANDARD
        .decode(body["data_b64"].as_str().unwrap())
        .unwrap();
    assert_eq!(&data[..5], b"hello", "snapshot is frozen at fork time");

    let (status, body) = call(&app, "GET", "/live/pages/0", None).await;
    assert_eq!(status, 200);
    let data = base64::engine::general_purpose::STANDARD
        .decode(body["data_b64"].as_str().unwrap())
        .unwrap();
    assert_eq!(&data[..5], b"WORLD");

    // Snapshot listing + info.
    let (status, body) = call(&app, "GET", "/snapshots", None).await;
    assert_eq!(status, 200);
    assert_eq!(body.as_array().unwrap().len(), 1);

    // Delete, then it is gone.
    let (status, _) = call(&app, "DELETE", &format!("/snapshots/{snap_id}"), None).await;
    assert_eq!(status, 200);
    let (status, body) = call(&app, "GET", &format!("/snapshots/{snap_id}"), None).await;
    assert_eq!(status, 409);
    assert_eq!(body["error"]["category"], "state_conflict");
    assert_eq!(body["error"]["code"], "snapshot_not_found");

    // Diagnostics.
    let (status, body) = call(&app, "GET", "/diag/stats", None).await;
    assert_eq!(status, 200);
    assert_eq!(body["quarantined"], false);
    assert!(body["objects_used"].as_u64().unwrap() >= 1);

    let (status, body) = call(
        &app,
        "POST",
        "/diag/audit",
        Some(serde_json::json!({ "enforce": true })),
    )
    .await;
    assert_eq!(status, 200);
    assert_eq!(body["ok"], true);

    let (status, body) = call(&app, "GET", "/diag/events?limit=10", None).await;
    assert_eq!(status, 200);
    assert!(
        !body.as_array().unwrap().is_empty(),
        "event log is populated"
    );
}

#[tokio::test]
async fn http_error_categories() {
    let app = make_app("http_error_categories", 32, 2, 2);

    // Bad base64 -> 400 input/bad_base64.
    let (status, body) = call(
        &app,
        "POST",
        "/live/writes",
        Some(serde_json::json!({ "writes": [{ "page": 0, "offset": 0, "data_b64": "!!!" }] })),
    )
    .await;
    assert_eq!(status, 400);
    assert_eq!(body["error"]["category"], "input");
    assert_eq!(body["error"]["code"], "bad_base64");

    // Page out of range -> 400 input/page_out_of_range.
    let (status, body) = call(
        &app,
        "POST",
        "/live/writes",
        Some(serde_json::json!({ "writes": [{ "page": 7, "offset": 0, "data_b64": b64(b"x") }] })),
    )
    .await;
    assert_eq!(status, 400);
    assert_eq!(body["error"]["category"], "input");
    assert_eq!(body["error"]["code"], "page_out_of_range");

    // Capacity is 3 objects. After writing page 0 (objects: zero + p0) and
    // forking, both pages are shared with the snapshot: a batch touching
    // both needs 2 new objects but only 3-2=1 is available -> 507.
    let app2 = make_app("http_error_categories_capacity", 32, 2, 3);
    let (status, _) = call(
        &app2,
        "POST",
        "/live/writes",
        Some(serde_json::json!({ "writes": [{ "page": 0, "offset": 0, "data_b64": b64(b"x") }] })),
    )
    .await;
    assert_eq!(status, 200);
    let (status, _) = call(&app2, "POST", "/snapshots", Some(serde_json::json!({}))).await;
    assert_eq!(status, 200);
    let (status, body) = call(
        &app2,
        "POST",
        "/live/writes",
        Some(serde_json::json!({ "writes": [
            { "page": 0, "offset": 0, "data_b64": b64(b"y") },
            { "page": 1, "offset": 0, "data_b64": b64(b"z") }
        ] })),
    )
    .await;
    assert_eq!(status, 507);
    assert_eq!(body["error"]["category"], "resource_exhausted");
    assert_eq!(body["error"]["code"], "capacity_exhausted");

    // Deleting an unknown snapshot -> 409 state_conflict.
    let (status, body) = call(&app, "DELETE", "/snapshots/42", None).await;
    assert_eq!(status, 409);
    assert_eq!(body["error"]["category"], "state_conflict");
    assert_eq!(body["error"]["code"], "snapshot_not_found");
}
