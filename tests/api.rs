//! HTTP-level integration tests: real axum router driven in-process with tower oneshot.

mod common;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use collate_agg::api;
use collate_agg::diag::Decision;
use collate_agg::state::AppState;
use common::test_state;
use tower::ServiceExt;

fn app() -> axum::Router {
    api::router(AppState::new(test_state().config.clone()))
}

async fn post_json(uri: &str, body: serde_json::Value) -> (StatusCode, serde_json::Value) {
    let resp = app()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(uri)
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = resp.status();
    let bytes = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: serde_json::Value = serde_json::from_slice(&bytes).unwrap_or_else(
        |_| serde_json::json!({"_raw": String::from_utf8_lossy(&bytes).to_string()}),
    );
    (status, json)
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
}

#[tokio::test]
async fn group_endpoint_accepts_and_compares() {
    let (status, body) = post_json(
        "/api/v1/group",
        serde_json::json!({
            "column": "label",
            "rule_version": "2026R1",
            "values": ["Café", "cafe", "CAFE", "item10", "item2", "ITEM02"]
        }),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "body={body}");
    assert_eq!(body["decision"], serde_json::json!("accepted"));
    let cmp = &body["comparison"];
    assert_eq!(cmp["partitions_agree"], true);
    assert_eq!(cmp["per_key_hashes_agree"], true);
    assert_eq!(cmp["sort_group_count"], 3); // cafe class + item2 class + item10
    assert_eq!(cmp["hash_group_count"], 3);
    assert_eq!(cmp["oracle_group_count"], 3);
    // Diagnostics carry a request id and stages.
    assert!(body["diagnostics"].is_array());
    assert!(!body["request_id"].as_str().unwrap().is_empty());
}

#[tokio::test]
async fn mixed_versions_conflict_409() {
    let (status, body) = post_json(
        "/api/v1/group",
        serde_json::json!({
            "column": "label",
            "rule_version": "2026R1",
            "row_rule_versions": ["2026R1", "2026R2"],
            "values": ["cafe", "CAFE"]
        }),
    )
    .await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["decision"], serde_json::json!("rejected"));
    assert_eq!(
        body["failure"]["kind"],
        serde_json::json!("rule_version_mixed")
    );
}

#[tokio::test]
async fn unknown_version_400() {
    let (status, body) = post_json(
        "/api/v1/group",
        serde_json::json!({"column": "label", "rule_version": "nope", "values": ["x"]}),
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(
        body["failure"]["kind"],
        serde_json::json!("unknown_rule_version")
    );
}

#[tokio::test]
async fn malformed_json_400_envelope() {
    let resp = app()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/v1/group")
                .header("content-type", "application/json")
                .body(Body::from("{ not json"))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn decision_enum_roundtrips() {
    // Cheap guard the decision tag is lowercase as the contract states.
    assert_eq!(
        serde_json::to_string(&Decision::Accepted).unwrap(),
        "\"accepted\""
    );
}
