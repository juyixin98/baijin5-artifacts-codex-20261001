//! HTTP integration tests through the real Axum router.

mod common;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use recursive_cte::config::Config;
use recursive_cte::server::router;
use tower::ServiceExt; // `.oneshot`

use common::id_graph_request;

async fn post_execute(json: &serde_json::Value) -> (StatusCode, serde_json::Value) {
    let app = router(Config::default());
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/execute")
                .header("content-type", "application/json")
                .body(Body::from(serde_json::to_string(json).unwrap()))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = resp.status();
    let bytes = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    let value = serde_json::from_slice(&bytes).unwrap_or_else(|_| {
        panic!(
            "non-JSON response body: {}",
            String::from_utf8_lossy(&bytes)
        )
    });
    (status, value)
}

#[tokio::test]
async fn health_reports_version_and_ok() {
    let app = router(Config::default());
    let resp = app
        .oneshot(
            Request::builder()
                .uri("/health")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["status"], "ok");
    assert_eq!(json["version"], env!("CARGO_PKG_VERSION"));
}

#[tokio::test]
async fn execute_tree_request_returns_explicit_success_envelope() {
    let req = id_graph_request(&[1], &[(1, 2), (1, 3)], "DISTINCT", "bfs");
    let (status, body) = post_execute(&req).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["outcome"], "ok");
    assert_eq!(body["status"], "complete");
    assert_eq!(body["complete"], true);
    assert_eq!(body["row_count"], 3);
    assert!(body["run_id"].as_str().unwrap().starts_with("run-"));
    assert_eq!(body["engine_version"], env!("CARGO_PKG_VERSION"));
    assert_eq!(
        body["columns"],
        serde_json::json!(["id", "path", "is_cycle"])
    );
    assert_eq!(
        body["rows"],
        serde_json::json!([[1, "[1]", false], [2, "[1,2]", false], [3, "[1,3]", false]])
    );
}

#[tokio::test]
async fn malformed_body_is_a_named_validation_error_not_success() {
    let app = router(Config::default());
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/execute")
                .header("content-type", "application/json")
                .body(Body::from("{ not json"))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["outcome"], "error");
    assert_eq!(json["category"], "validation_error");
    assert!(json["message"].as_str().unwrap().contains("malformed JSON"));
}

#[tokio::test]
async fn invalid_plan_returns_400_with_run_id_null() {
    let mut req = id_graph_request(&[1], &[(1, 2)], "ALL", "bfs");
    req["key_columns"] = serde_json::json!(["missing"]);
    let (status, body) = post_execute(&req).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["outcome"], "error");
    assert_eq!(body["category"], "validation_error");
    assert!(body["run_id"].is_null());
}

#[tokio::test]
async fn incomplete_run_is_still_http_200_but_explicitly_flagged() {
    let mut req = id_graph_request(&[1], &[(1, 2), (2, 3), (3, 4)], "ALL", "bfs");
    req["limits"] = serde_json::json!({"max_depth": 1});
    let (status, body) = post_execute(&req).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["outcome"], "ok");
    assert_eq!(body["status"], "incomplete_max_depth");
    assert_eq!(body["complete"], false);
}
