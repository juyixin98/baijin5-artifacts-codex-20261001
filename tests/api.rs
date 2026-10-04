//! Service-level tests: exercise the Axum API in-process (no network),
//! asserting concrete response bodies and HTTP status classes.

mod common;

use axum::body::{to_bytes, Body};
use axum::http::{Request, StatusCode};
use base64::{engine::general_purpose::STANDARD as B64, Engine};
use common::TestLog;
use rib::api::{router, AppState};
use rib::budget::DecodeBudget;
use rib::encode::EncodeOptions;
use tower::ServiceExt;

fn test_state() -> AppState {
    AppState {
        budget: DecodeBudget::default(),
        encode_options: EncodeOptions::default(),
    }
}

async fn post_json(app: &axum::Router, uri: &str, body: serde_json::Value) -> (StatusCode, serde_json::Value) {
    let req = Request::builder()
        .method("POST")
        .uri(uri)
        .header("content-type", "application/json")
        .body(Body::from(serde_json::to_vec(&body).unwrap()))
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    let status = resp.status();
    let bytes = to_bytes(resp.into_body(), usize::MAX).await.unwrap();
    (status, serde_json::from_slice(&bytes).unwrap())
}

#[tokio::test]
async fn encode_then_decode_roundtrip_over_http() {
    let log = TestLog::new("api-roundtrip");
    let app = router(test_state());

    let mut values: Vec<u64> = vec![555; 64];
    values.extend_from_slice(&[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    log.step("input", &format!("values={}", values.len()));

    let (status, enc) = post_json(
        &app,
        "/v1/encode",
        serde_json::json!({"values": values}),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(enc["input_values"], values.len());
    assert!(enc["block_count"].as_u64().unwrap() >= 2, "expected mixed blocks");
    log.step(
        "encode",
        &format!("blocks={} bytes={}", enc["block_count"], enc["output_bytes"]),
    );

    let (status, dec) = post_json(
        &app,
        "/v1/decode",
        serde_json::json!({"column_b64": enc["column_b64"]}),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let got: Vec<u64> = serde_json::from_value(dec["values"].clone()).unwrap();
    assert_eq!(got, values);
    log.verdict(true, "HTTP encode -> decode equals original column");
}

#[tokio::test]
async fn corrupted_column_returns_structured_422() {
    let log = TestLog::new("api-corrupt");
    let app = router(test_state());

    let mut bytes = rib::encode::encode_column(&[7u64; 32], &EncodeOptions::default());
    bytes[6] = 65; // illegal bit width
    let (status, body) = post_json(
        &app,
        "/v1/decode",
        serde_json::json!({"column_b64": B64.encode(&bytes)}),
    )
    .await;
    log.step("response", &format!("status={} body={}", status, body));
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["category"], "invalid_bit_width");
    assert_eq!(body["error"]["offset"], 6);
    log.verdict(true, "corrupt block -> 422 with category and offset");
}

#[tokio::test]
async fn budget_violation_returns_413() {
    let log = TestLog::new("api-budget");
    let state = AppState {
        budget: DecodeBudget {
            max_values_per_block: 4,
            ..DecodeBudget::default()
        },
        ..test_state()
    };
    let app = router(state);

    let bytes = rib::encode::encode_column(&[3u64; 100], &EncodeOptions::default());
    let (status, body) = post_json(
        &app,
        "/v1/decode",
        serde_json::json!({"column_b64": B64.encode(&bytes)}),
    )
    .await;
    log.step("response", &format!("status={} body={}", status, body));
    assert_eq!(status, StatusCode::PAYLOAD_TOO_LARGE);
    assert_eq!(body["error"]["category"], "value_count_over_budget");
    log.verdict(true, "budget violation -> 413, not success");
}

#[tokio::test]
async fn invalid_base64_is_an_error_not_success() {
    let app = router(test_state());
    let (status, body) = post_json(
        &app,
        "/v1/decode",
        serde_json::json!({"column_b64": "!!!not-base64!!!"}),
    )
    .await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert!(body["error"]["category"].is_string());
}

#[tokio::test]
async fn health_and_version_endpoints() {
    let app = router(test_state());
    for uri in ["/health", "/version"] {
        let req = Request::builder().uri(uri).body(Body::empty()).unwrap();
        let resp = app.clone().oneshot(req).await.unwrap();
        assert_eq!(resp.status(), StatusCode::OK, "{}", uri);
    }
}
