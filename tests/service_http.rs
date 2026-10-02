//! Service-level tests: HTTP status codes map to error categories, and the
//! cancel endpoint turns a running query into a 499.

mod common;

use std::sync::Arc;
use std::time::Duration;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use axum::Router;
use pullq::service::{router, AppState};
use tower::ServiceExt;

use common::tlog;

fn test_app() -> (Router, Arc<AppState>, tempfile::TempDir) {
    let dir = tempfile::tempdir().expect("tempdir");
    let state = Arc::new(AppState::new(1 << 20, dir.path().join("spill")));
    (router(Arc::clone(&state)), state, dir)
}

async fn post_json(app: Router, uri: &str, body: serde_json::Value) -> (StatusCode, serde_json::Value) {
    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(uri)
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .expect("request"),
        )
        .await
        .expect("response");
    let status = response.status();
    let bytes = axum::body::to_bytes(response.into_body(), 1 << 20).await.expect("body");
    let json = serde_json::from_slice(&bytes).unwrap_or(serde_json::json!({}));
    (status, json)
}

async fn get_json(app: Router, uri: &str) -> serde_json::Value {
    let response = app
        .oneshot(Request::builder().uri(uri).body(Body::empty()).expect("request"))
        .await
        .expect("response");
    let bytes = axum::body::to_bytes(response.into_body(), 1 << 20).await.expect("body");
    serde_json::from_slice(&bytes).expect("json")
}

#[tokio::test]
async fn invalid_plan_maps_to_400_with_input_category() {
    let run_id = pullq::exec::RunId::new().to_string();
    let (app, _state, _dir) = test_app();
    let (status, body) = post_json(
        app,
        "/query",
        serde_json::json!({ "plan": { "op": "scan", "table": "nope" } }),
    )
    .await;
    tlog!(run_id, "invalid plan -> {status} {body}");
    assert_eq!(status, StatusCode::BAD_REQUEST, "[{run_id}] validation failure is HTTP 400");
    assert_eq!(body["category"], "input", "[{run_id}] category is machine-readable");
}

#[tokio::test]
async fn sort_query_returns_sorted_rows_and_clean_metrics() {
    let run_id = pullq::exec::RunId::new().to_string();
    let (app, state, _dir) = test_app();
    let (status, body) = post_json(
        app.clone(),
        "/query",
        serde_json::json!({
            "plan": { "op": "sort", "key": "k",
                      "input": { "op": "scan", "table": "numbers", "batches": 4, "batch_rows": 64, "seed": 9 } },
            "timeout_ms": 5000
        }),
    )
    .await;
    tlog!(run_id, "sort query -> {status}, rows={}", body["row_count"]);
    assert_eq!(status, StatusCode::OK, "[{run_id}] valid query succeeds: {body}");
    assert_eq!(body["row_count"], 256, "[{run_id}] all rows returned");
    let keys: Vec<i64> = body["rows"].as_array().expect("rows").iter().map(|r| r[0].as_i64().unwrap()).collect();
    assert!(keys.windows(2).all(|w| w[0] <= w[1]), "[{run_id}] HTTP response rows are sorted");
    // Response metrics prove the query left nothing behind.
    assert_eq!(body["metrics"]["memory_used_bytes"], 0, "[{run_id}] memory reclaimed");
    assert_eq!(body["metrics"]["tasks_live"], 0, "[{run_id}] tasks reclaimed");

    let metrics = get_json(app, "/metrics").await;
    tlog!(run_id, "metrics after query: {metrics}");
    assert_eq!(metrics["active_queries"], 0, "[{run_id}] no live queries after completion");
    assert_eq!(state.queries_finished.load(std::sync::atomic::Ordering::Acquire), 1, "[{run_id}] finished counter");
}

#[tokio::test]
async fn timeout_maps_to_408_with_timeout_category() {
    let run_id = pullq::exec::RunId::new().to_string();
    let (app, _state, _dir) = test_app();
    let (status, body) = post_json(
        app,
        "/query",
        serde_json::json!({
            "plan": { "op": "scan", "table": "numbers", "batches": 50, "batch_rows": 8,
                      "delay_ms_per_batch": 100 },
            "timeout_ms": 250
        }),
    )
    .await;
    tlog!(run_id, "timeout query -> {status} {body}");
    assert_eq!(status, StatusCode::REQUEST_TIMEOUT, "[{run_id}] deadline expiry is HTTP 408");
    assert_eq!(body["category"], "cancelled_timeout", "[{run_id}] timeout category distinct from user cancel");
}

#[tokio::test]
async fn row_limit_maps_to_507_with_resource_category() {
    let run_id = pullq::exec::RunId::new().to_string();
    let (app, _state, _dir) = test_app();
    let (status, body) = post_json(
        app,
        "/query",
        serde_json::json!({
            "plan": { "op": "scan", "table": "numbers", "batches": 4, "batch_rows": 64 },
            "max_rows": 100
        }),
    )
    .await;
    tlog!(run_id, "row-limit query -> {status} {body}");
    assert_eq!(status, StatusCode::INSUFFICIENT_STORAGE, "[{run_id}] row limit is HTTP 507");
    assert_eq!(body["category"], "resource_exhausted", "[{run_id}] resource category");
}

#[tokio::test]
async fn user_cancel_maps_to_499_with_user_category() {
    let run_id = pullq::exec::RunId::new().to_string();
    let (app, state, _dir) = test_app();

    // Start a slow query in the background.
    let query_app = app.clone();
    let query = tokio::spawn(async move {
        post_json(
            query_app,
            "/query",
            serde_json::json!({
                "plan": { "op": "scan", "table": "numbers", "batches": 50, "batch_rows": 8,
                          "delay_ms_per_batch": 100 }
            }),
        )
        .await
    });

    // Wait for it to register, then cancel by run id.
    let query_run_id = {
        let mut found = None;
        for _ in 0..50 {
            tokio::time::sleep(Duration::from_millis(20)).await;
            let id = state.live.lock().expect("live").keys().next().cloned();
            if id.is_some() {
                found = id;
                break;
            }
        }
        found.expect("query registered")
    };
    tlog!(run_id, "cancelling live query {query_run_id}");
    let (cancel_status, _) = post_json(app, &format!("/query/{query_run_id}/cancel"), serde_json::json!({})).await;
    assert_eq!(cancel_status, StatusCode::OK, "[{run_id}] cancel accepted");

    let (status, body) = query.await.expect("query task joins");
    tlog!(run_id, "cancelled query -> {status} {body}");
    assert_eq!(status, StatusCode::from_u16(499).unwrap(), "[{run_id}] user cancel is HTTP 499");
    assert_eq!(body["category"], "cancelled_user", "[{run_id}] user cancel category distinct from timeout");
}
