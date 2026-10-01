//! End-to-end HTTP tests against the real Axum router (no network sockets:
//! tower `oneshot` drives the service in-process).
use axum::body::Body;
use axum::http::{Request, StatusCode};
use leapfrog_triejoin::{build_default_catalog, router, AppState, ServerConfig};
use tower::ServiceExt;

fn app() -> axum::Router {
    let state = AppState::new(ServerConfig::default(), build_default_catalog());
    router(state)
}

async fn post_json(
    router: &axum::Router,
    body: serde_json::Value,
) -> (StatusCode, serde_json::Value) {
    let resp = router
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/v1/join")
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = resp.status();
    let bytes = axum::body::to_bytes(resp.into_body(), 16 * 1024 * 1024)
        .await
        .unwrap();
    let json: serde_json::Value = serde_json::from_slice(&bytes).unwrap_or_default();
    (status, json)
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
}

#[tokio::test]
async fn triangle_fixture_returns_24() {
    let body = serde_json::json!({
        "fixtures": ["tri_e", "tri_f", "tri_g"],
        "limit": 1000
    });
    let (status, json) = post_json(&app(), body).await;
    assert_eq!(status, StatusCode::OK, "{json}");
    assert_eq!(json["decision"], "accepted");
    assert_eq!(json["row_count"], 24);
    assert_eq!(json["counters"]["emitted_multiplicity"], 24);
    // Diagnostics carry a correlation id and redaction note, never row data.
    assert!(json["request_id"].as_str().unwrap().len() >= 8);
    assert!(json["diagnostics"]["redaction"]
        .as_str()
        .unwrap()
        .contains("never logged"));
}

#[tokio::test]
async fn cartesian_product_is_400_with_code() {
    let body = serde_json::json!({
        "relations": [
            {"name": "a", "schema": [{"name":"x","type":"int64"}], "rows": [[1]]},
            {"name": "b", "schema": [{"name":"z","type":"int64"}], "rows": [[2]]}
        ]
    });
    let (status, json) = post_json(&app(), body).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(json["decision"], "rejected");
    assert_eq!(json["error_code"], "disconnected_join_graph");
    assert_eq!(json["error_category"], "validation");
}

#[tokio::test]
async fn pagination_headers_round_trip_on_arrow_endpoint() {
    let body = serde_json::json!({
        "fixtures": ["skew_ab", "skew_ac", "pick_b"],
        "limit": 100
    });
    let resp = app()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/v1/join/arrow")
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    assert_eq!(
        resp.headers().get("x-lfj-truncated").unwrap(),
        "true",
        "1000 skew rows behind a limit of 100 must be truncated/resumable"
    );
    assert!(resp.headers().contains_key("x-lfj-next-cursor"));
    assert_eq!(
        resp.headers().get("content-type").unwrap(),
        "application/vnd.apache.arrow.stream"
    );
    let bytes = axum::body::to_bytes(resp.into_body(), 16 * 1024 * 1024)
        .await
        .unwrap();
    assert!(bytes.len() > 100, "expected a non-empty Arrow IPC stream");
}

#[tokio::test]
async fn oversized_body_is_rejected() {
    // Default limit is 16 MiB; send a JSON array clearly beyond it.
    let big = "x".repeat(20 * 1024 * 1024);
    let body = format!("{{\"relations\":[{{\"name\":\"a\",\"schema\":[{{\"name\":\"k\",\"type\":\"utf8\"}}],\"rows\":[[\"{big}\"]]}}]}}");
    let resp = app()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/v1/join")
                .header("content-type", "application/json")
                .body(Body::from(body))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::PAYLOAD_TOO_LARGE);
}

#[tokio::test]
async fn caller_supplied_request_id_is_echoed() {
    let body = serde_json::json!({
        "request_id": "corr-xyz-123",
        "fixtures": ["dup_l", "dup_r"]
    });
    let (status, json) = post_json(&app(), body).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(json["request_id"], "corr-xyz-123");
    // Multiplicity 3*2 = 6 verified end to end.
    assert_eq!(json["rows"][0]["multiplicity"], 6);
}
