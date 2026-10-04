//! End-to-end API tests through the Axum router (in-process, no socket).
//! Each test asserts concrete status codes, error categories and payload
//! content — not merely that the endpoint responds.

use axum::body::Body;
use axum::http::{Request, StatusCode};
use base64::{engine::general_purpose::STANDARD as B64, Engine};
use iblt_service::format;
use iblt_service::iblt::{Iblt, Params};
use iblt_service::limits::Limits;
use iblt_service::service;
use serde_json::{json, Value};
use tower::ServiceExt;

fn app() -> axum::Router {
    service::app(Limits::default())
}

async fn post(app: axum::Router, path: &str, body: Value) -> (StatusCode, Value) {
    let resp = app
        .oneshot(
            Request::post(path)
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = resp.status();
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20).await.unwrap();
    let json: Value = serde_json::from_slice(&bytes)
        .unwrap_or_else(|e| panic!("response is not JSON ({e}): {}", String::from_utf8_lossy(&bytes)));
    (status, json)
}

fn encode_table_b64(keys: &[u64], cells: u32) -> String {
    let mut t = Iblt::new(Params { cells, k: 3, seed: 0x5EED }).unwrap();
    for &k in keys {
        t.insert(k);
    }
    B64.encode(format::serialize(&t))
}

#[tokio::test]
async fn health_is_ok() {
    let resp = app()
        .oneshot(Request::get("/health").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
}

#[tokio::test]
async fn encode_then_decode_roundtrip_recovers_both_sides() {
    // A = 1..=12, B = 7..=18 -> only_a = 1..=6, only_b = 13..=18
    let (status, enc_a) = post(app(), "/v1/encode", json!({"keys": (1..=12).collect::<Vec<u64>>()})).await;
    assert_eq!(status, StatusCode::OK, "encode A failed: {enc_a}");
    let (status, enc_b) = post(app(), "/v1/encode", json!({"keys": (7..=18).collect::<Vec<u64>>()})).await;
    assert_eq!(status, StatusCode::OK, "encode B failed: {enc_b}");

    // Both encodes used the same default sizing (12 keys each) so params match.
    assert_eq!(enc_a["params"], enc_b["params"]);

    let (status, dec) = post(
        app(),
        "/v1/decode",
        json!({"table_a_b64": enc_a["table_b64"], "table_b_b64": enc_b["table_b64"]}),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "decode failed: {dec}");
    assert_eq!(dec["complete"], true);
    assert_eq!(dec["only_a"], json!(vec![1u64, 2, 3, 4, 5, 6]));
    assert_eq!(dec["only_b"], json!(vec![13u64, 14, 15, 16, 17, 18]));
    assert_eq!(dec["stats"]["peeled"], 12);
    assert!(dec["run_id"].as_str().unwrap().starts_with("run-"));
}

#[tokio::test]
async fn encode_rejects_duplicate_keys_as_invalid_input() {
    let (status, body) = post(app(), "/v1/encode", json!({"keys": [1, 2, 2, 3]})).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"]["category"], "invalid_input");
    assert_eq!(body["error"]["code"], "invalid_input");
    assert!(body["error"]["message"].as_str().unwrap().contains("duplicate"));
    assert!(body["run_id"].as_str().unwrap().starts_with("run-"));
}

#[tokio::test]
async fn encode_enforces_key_limit_as_resource_exhausted() {
    let tight = Limits { max_keys: 4, ..Limits::default() };
    let (status, body) = post(
        service::app(tight),
        "/v1/encode",
        json!({"keys": [1, 2, 3, 4, 5]}),
    )
    .await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["category"], "resource_exhausted");
}

#[tokio::test]
async fn decode_incomplete_is_422_with_hint_and_no_partial_sets() {
    // 40 differing keys in 16 cells cannot decode.
    let a = encode_table_b64(&(1..=40).collect::<Vec<u64>>(), 16);
    let b = encode_table_b64(&[], 16);
    let (status, body) = post(app(), "/v1/decode", json!({"table_a_b64": a, "table_b_b64": b})).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["category"], "resource_exhausted");
    assert_eq!(body["error"]["code"], "decode_incomplete");
    assert!(body["error"]["detail"]["remaining_nonzero_cells"].as_u64().unwrap() > 0);
    assert!(body["error"]["detail"]["hint"].as_str().unwrap().contains("larger")
        || body["error"]["detail"]["hint"].as_str().unwrap().contains("more cells"));
    assert!(body.get("only_a").is_none(), "partial results must not leak");
    assert!(body.get("only_b").is_none());
}

#[tokio::test]
async fn decode_with_mismatched_params_is_state_conflict() {
    let a = encode_table_b64(&[1, 2, 3], 16);
    let b = encode_table_b64(&[1, 2, 3], 32);
    let (status, body) = post(app(), "/v1/decode", json!({"table_a_b64": a, "table_b_b64": b})).await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["error"]["category"], "state_conflict");
}

#[tokio::test]
async fn decode_rejects_bad_base64_and_bad_bytes_as_invalid_input() {
    let good = encode_table_b64(&[1], 16);
    let (status, body) =
        post(app(), "/v1/decode", json!({"table_a_b64": "!!!not-base64!!!", "table_b_b64": good})).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"]["category"], "invalid_input");

    let garbage = B64.encode(b"not an iblt table at all");
    let (status, body) =
        post(app(), "/v1/decode", json!({"table_a_b64": garbage, "table_b_b64": good})).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"]["category"], "invalid_input");
}

#[tokio::test]
async fn malformed_json_body_is_invalid_input() {
    let resp = app()
        .oneshot(
            Request::post("/v1/encode")
                .header("content-type", "application/json")
                .body(Body::from("{not json"))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20).await.unwrap();
    let body: Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(body["error"]["category"], "invalid_input");
}

#[tokio::test]
async fn subtract_endpoint_returns_combinable_difference_table() {
    let a = encode_table_b64(&[10, 20, 30], 32);
    let b = encode_table_b64(&[20, 30, 40], 32);
    let (status, sub) = post(app(), "/v1/subtract", json!({"table_a_b64": a, "table_b_b64": b})).await;
    assert_eq!(status, StatusCode::OK, "subtract failed: {sub}");

    // The returned difference table must itself decode to the two sides.
    let empty = encode_table_b64(&[], 32);
    let (status, dec) = post(
        app(),
        "/v1/decode",
        json!({"table_a_b64": sub["table_b64"], "table_b_b64": empty}),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "decode of difference failed: {dec}");
    assert_eq!(dec["only_a"], json!(vec![10u64]));
    assert_eq!(dec["only_b"], json!(vec![40u64]));
}
