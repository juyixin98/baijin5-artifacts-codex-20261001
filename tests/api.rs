//! End-to-end HTTP tests through the real Axum router (no network sockets).

mod common;

use std::sync::Arc;

use axum::body::{to_bytes, Body};
use axum::http::{Request, StatusCode};
use collation_agg_contract::{api, AppState, Settings};
use serde_json::{json, Value};
use tower::ServiceExt;

fn app() -> axum::Router {
    api::router(Arc::new(AppState::new(Settings::default())))
}

async fn body_json(resp: axum::response::Response) -> Value {
    let bytes = to_bytes(resp.into_body(), usize::MAX).await.unwrap();
    serde_json::from_slice(&bytes).unwrap()
}

fn accent_payload() -> Value {
    json!({
        "request_id": "http-1",
        "rule_version": 1,
        "rows": [
            {"record_id": "r1", "value": "Café"},
            {"record_id": "r2", "value": "cafe\u{0301}"},
            {"record_id": "r3", "value": "CAFE"}
        ],
        "expect_group_count": 1
    })
}

#[tokio::test]
async fn health_is_ok() {
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
    let v = body_json(resp).await;
    assert_eq!(v["status"], "ok");
}

#[tokio::test]
async fn verify_accepts_and_compares_both_strategies() {
    let resp = app()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/verify")
                .header("content-type", "application/json")
                .body(Body::from(accent_payload().to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let v = body_json(resp).await;
    assert_eq!(v["status"], "accepted");
    assert_eq!(v["category"], "accepted");
    assert_eq!(v["group_count"], 1);
    // Representative retained verbatim.
    assert_eq!(v["sort_groups"][0]["representative"], "Café");
    // Both executors present and identical.
    assert_eq!(v["sort_groups"], v["hash_groups"]);
}

#[tokio::test]
async fn unknown_rule_returns_categorized_rejection() {
    let payload = json!({
        "request_id": "http-bad",
        "rule_version": 99,
        "rows": [{"record_id": "x", "value": "a"}]
    });
    let resp = app()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/verify")
                .header("content-type", "application/json")
                .body(Body::from(payload.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    // Rejection is a normal verdict at HTTP 200.
    assert_eq!(resp.status(), StatusCode::OK);
    let v = body_json(resp).await;
    assert_eq!(v["status"], "rejected");
    assert_eq!(v["category"], "reject_unknown_rule");
    assert!(v["group_count"].is_null());
}

#[tokio::test]
async fn diagnostics_carry_request_id_and_no_raw_values() {
    let application = Arc::new(AppState::new(Settings::default()));
    let app = api::router(application.clone());

    let post = Request::builder()
        .method("POST")
        .uri("/verify")
        .header("content-type", "application/json")
        .body(Body::from(accent_payload().to_string()))
        .unwrap();
    app.oneshot(post).await.unwrap();

    // Re-query diagnostics on a fresh oneshot of the same stateful app.
    let app = api::router(application.clone());
    let resp = app
        .oneshot(
            Request::builder()
                .uri("/diagnostics")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let v = body_json(resp).await;
    let records = v["records"].as_array().unwrap();
    assert!(!records.is_empty());
    let last = records.last().unwrap();
    assert_eq!(last["request_id"], "http-1");
    assert_eq!(last["reason_code"], "accepted");
    assert_eq!(last["rows"], 3);
    // Diagnostic text must never embed a raw value.
    let detail = last["detail"].as_str().unwrap();
    assert!(!detail.contains("Café"));
    assert!(!detail.contains("CAFE"));
}

#[tokio::test]
async fn sensitive_values_are_redacted_in_response() {
    let payload = json!({
        "request_id": "http-secret",
        "rule_version": 1,
        "sensitive": true,
        "rows": [{"record_id": "r1", "value": "TOPSECRET_Café"}]
    });
    let resp = app()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/verify")
                .header("content-type", "application/json")
                .body(Body::from(payload.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let v = body_json(resp).await;
    let rep = v["sort_groups"][0]["representative"].as_str().unwrap();
    assert!(rep.contains("<str bytes="), "got {rep}");
    assert!(!rep.contains("TOPSECRET"));
}
