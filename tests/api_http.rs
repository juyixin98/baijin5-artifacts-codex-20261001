//! HTTP boundary tests: the API exposes the same semantics and error
//! categories as the engine, and every error body carries a run id.

mod common;

use std::sync::Arc;

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use base64::{Engine as _, engine::general_purpose::STANDARD as B64};
use common::{RunLog, open_engine, pattern};
use cow_snap::api::{self, AppState};
use serde_json::{Value, json};
use tokio::sync::Mutex;
use tower::ServiceExt;

fn app(capacity: usize) -> axum::Router {
    let tmp = tempfile::tempdir().unwrap();
    // Leak the tempdir handle into the state closure scope: the test
    // process is short-lived and this keeps the data dir alive.
    let dir = tmp.keep();
    let engine = open_engine(&dir, capacity);
    api::router(Arc::new(AppState {
        engine: Mutex::new(engine),
        service_run_id: "test-service-run".into(),
    }))
}

async fn call(app: &axum::Router, req: Request<Body>) -> (StatusCode, Value) {
    let resp = app.clone().oneshot(req).await.unwrap();
    let status = resp.status();
    let bytes = to_bytes(resp.into_body(), usize::MAX).await.unwrap();
    let body = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
    (status, body)
}

fn post_json(uri: &str, body: Value) -> Request<Body> {
    Request::post(uri)
        .header("content-type", "application/json")
        .body(Body::from(body.to_string()))
        .unwrap()
}

#[tokio::test]
async fn http_api_roundtrip_and_error_categories() {
    let mut log = RunLog::new("http_api_roundtrip_and_error_categories");
    let app = app(8);

    // Create base snapshot.
    let (status, body) = call(&app, post_json("/snapshots", json!({}))).await;
    assert_eq!(status, StatusCode::CREATED);
    let base = body["snapshot_id"].as_u64().unwrap();
    assert!(body["run_id"].is_string(), "responses carry a run id");

    // Write a page through the API.
    let (status, body) = call(
        &app,
        post_json(
            &format!("/snapshots/{base}/writes"),
            json!({"writes": [{"page": 0, "offset": 0, "data_b64": B64.encode(pattern(b'H'))}]}),
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "got: {body}");
    assert_eq!(body["pages_written"], 1);
    assert_eq!(body["fresh_allocs"], 1);

    // Read it back.
    let (status, body) = call(
        &app,
        Request::get(format!("/snapshots/{base}/pages/0")).body(Body::empty()).unwrap(),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(B64.decode(body["data_b64"].as_str().unwrap()).unwrap(), pattern(b'H'));
    log.event("roundtrip", json!({"snapshot": base, "page": 0}),
              "HTTP write/read roundtrip preserves page content");

    // Input error: page index out of range -> 400 + category "input".
    let (status, body) = call(
        &app,
        post_json(
            &format!("/snapshots/{base}/writes"),
            json!({"writes": [{"page": 99, "offset": 0, "data_b64": B64.encode(b"x")}]}),
        ),
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"]["category"], "input");
    assert!(body["error"]["run_id"].is_string());

    // State conflict: unknown snapshot -> 409 + "state_conflict".
    let (status, body) = call(
        &app,
        Request::delete("/snapshots/999").body(Body::empty()).unwrap(),
    )
    .await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["error"]["category"], "state_conflict");

    // Resource exhausted: capacity 8, fill 8 pages, then one more.
    let writes: Vec<Value> = (1..8)
        .map(|p| json!({"page": p, "offset": 0, "data_b64": B64.encode(pattern(b'F'))}))
        .collect();
    let (status, _) = call(
        &app,
        post_json(&format!("/snapshots/{base}/writes"), json!({"writes": writes})),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let (status, body) = call(
        &app,
        post_json(
            &format!("/snapshots/{base}/writes"),
            json!({"writes": [{"page": 0, "offset": 0, "data_b64": B64.encode(b"z")}]}),
        ),
    )
    .await;
    // Page 0 is exclusively owned, so this is an in-place write and fits.
    assert_eq!(status, StatusCode::OK, "in-place rewrite fits at capacity: {body}");
    // Forking shares pages (no allocation), then a COW write must fail.
    let (status, body) = call(&app, post_json("/snapshots", json!({"parent": base}))).await;
    assert_eq!(status, StatusCode::CREATED);
    let child = body["snapshot_id"].as_u64().unwrap();
    let (status, body) = call(
        &app,
        post_json(
            &format!("/snapshots/{child}/writes"),
            json!({"writes": [{"page": 0, "offset": 0, "data_b64": B64.encode(b"z")}]}),
        ),
    )
    .await;
    assert_eq!(status, StatusCode::INSUFFICIENT_STORAGE);
    assert_eq!(body["error"]["category"], "resource_exhausted");
    log.event("categories_over_http", json!({"input": 400, "state": 409, "resource": 507}),
              "HTTP status and error category align for all three failure kinds");

    // Diagnostics endpoints.
    let (status, body) = call(&app, Request::get("/diag/verify").body(Body::empty()).unwrap()).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["ok"], true);
    let (status, body) = call(&app, Request::get("/diag/stats").body(Body::empty()).unwrap()).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["used_pages"], 8);
    assert_eq!(body["snapshot_count"], 2);
    log.finish();
}
