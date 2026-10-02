//! In-process HTTP tests against the real router (no open port), asserting
//! status codes, error categories in the body, and the cancel-vs-timeout
//! distinction over HTTP.

use axum::body::{to_bytes, Body};
use axum::http::{Request, StatusCode};
use tower::ServiceExt; // oneshot

fn app() -> axum::Router {
    pull_query::http::app()
}

async fn body_json(resp: axum::response::Response) -> serde_json::Value {
    let bytes = to_bytes(resp.into_body(), 1 << 20).await.unwrap();
    serde_json::from_slice(&bytes).unwrap()
}

#[tokio::test]
async fn health_ok() {
    let resp = app()
        .oneshot(
            Request::builder()
                .uri("/health")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let j = body_json(resp).await;
    assert_eq!(j["status"], "ok");
}

#[tokio::test]
async fn query_scan_ok_with_run_id_and_stats() {
    let resp = app()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/query")
                .header("content-type", "application/json")
                .body(Body::from(r#"{"source":{"table":"users"},"batch_size":3}"#))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let j = body_json(resp).await;
    assert_eq!(j["ok"], true);
    assert_eq!(j["stats"]["rows_out"], 8);
    assert!(j["run_id"].as_str().unwrap().starts_with("run-"));
    assert_eq!(j["stats"]["open_files_after_close"], 0);
}

#[tokio::test]
async fn query_spill_sort_succeeds_and_cleans_up() {
    let payload = serde_json::json!({
        "source": {"table": "orders", "rows": 50},
        "batch_size": 6,
        "sort_keys": ["amount"],
        "sort_memory_budget_bytes": 120
    });
    let resp = app()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/query")
                .header("content-type", "application/json")
                .body(Body::from(payload.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let j = body_json(resp).await;
    assert!(j["stats"]["runs_spilled"].as_u64().unwrap() >= 3);
    assert_eq!(j["stats"]["open_files_after_close"], 0);
    assert_eq!(j["stats"]["buffered_bytes_after_close"], 0);
    assert_eq!(j["rows"].as_array().unwrap().len(), 50);
}

#[tokio::test]
async fn invalid_plan_is_400_with_invalid_input_kind() {
    let resp = app()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/query")
                .header("content-type", "application/json")
                .body(Body::from(r#"{"sort_keys":["nope"]}"#))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let j = body_json(resp).await;
    assert_eq!(j["error"]["kind"], "invalid_input");
}

#[tokio::test]
async fn timeout_scenario_is_408() {
    let resp = app()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/validate/timeout")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::REQUEST_TIMEOUT);
    let j = body_json(resp).await;
    assert_eq!(j["error"]["kind"], "timeout");
    assert_ne!(j["error"]["kind"], "cancelled");
}

#[tokio::test]
async fn cancel_scenario_is_conflict_and_cancelled_kind() {
    let resp = app()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/validate/cancel")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::CONFLICT);
    let j = body_json(resp).await;
    assert_eq!(j["error"]["kind"], "cancelled");
    assert_eq!(j["resources"]["open_files_after_close"], 0);
}

#[tokio::test]
async fn stream_cancel_returns_ndjson_with_cancelled_terminal() {
    let resp = app()
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/query/stream?batches=50&delay_ms=20&cancel_after_ms=40")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::CONFLICT);
    assert_eq!(resp.headers()["content-type"], "application/x-ndjson");
    let bytes = to_bytes(resp.into_body(), 1 << 20).await.unwrap();
    let text = String::from_utf8(bytes.to_vec()).unwrap();
    let lines: Vec<&str> = text.lines().collect();
    let last: serde_json::Value = serde_json::from_str(lines.last().unwrap()).unwrap();
    assert_eq!(last["event"], "stats");
    assert_eq!(last["error"]["kind"], "cancelled");
    // Some rows were emitted before cancel and remain present/readable.
    assert!(lines.len() >= 2, "expected some row events before cancel");
    assert_eq!(last["resources"]["open_files_after_close"], 0);
}

#[tokio::test]
async fn unknown_scenario_is_404() {
    let resp = app()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/validate/nope")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::NOT_FOUND);
}
