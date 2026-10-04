//! HTTP API integration tests. Each test asserts concrete response bodies
//! and error categories, not just "the endpoint responds".

mod common;

use std::sync::Arc;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use base64::Engine;
use cdc_service::api::{build_router, AppState};
use cdc_service::limits::Limits;
use cdc_service::params::ChunkParams;
use common::SplitMix;
use serde_json::{json, Value};
use sha2::Digest;
use tower::ServiceExt;

fn test_state() -> Arc<AppState> {
    Arc::new(AppState::new(
        ChunkParams { min_size: 256, avg_bits: 10, max_size: 4096 },
        Limits { max_body_bytes: 1 << 20, max_chunks: 1000, max_concurrent: 8, request_timeout: std::time::Duration::from_secs(5) },
    ))
}

async fn json_body(resp: axum::response::Response) -> Value {
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 22).await.unwrap();
    serde_json::from_slice(&bytes).unwrap()
}

fn post(uri: &str, body: Body) -> Request<Body> {
    Request::builder().method("POST").uri(uri).body(body).unwrap()
}

fn post_json(uri: &str, value: &Value) -> Request<Body> {
    Request::builder()
        .method("POST")
        .uri(uri)
        .header("content-type", "application/json")
        .body(Body::from(serde_json::to_vec(value).unwrap()))
        .unwrap()
}

#[tokio::test]
async fn healthz_reports_ok() {
    let app = build_router(test_state());
    let resp = app.oneshot(Request::builder().uri("/healthz").body(Body::empty()).unwrap()).await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    assert_eq!(json_body(resp).await, json!({"status": "ok"}));
}

#[tokio::test]
async fn params_endpoint_advertises_algorithm() {
    let app = build_router(test_state());
    let resp = app.oneshot(Request::builder().uri("/v1/params").body(Body::empty()).unwrap()).await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = json_body(resp).await;
    assert_eq!(body["algorithm"]["name"], "gear-cdc");
    assert_eq!(body["algorithm"]["avg_bits"], 10);
    assert_eq!(body["algorithm"]["min_size"], 256);
    assert_eq!(body["algorithm"]["max_size"], 4096);
    assert_eq!(body["limits"]["max_chunks"], 1000);
}

#[tokio::test]
async fn every_response_carries_request_id() {
    let app = build_router(test_state());
    let resp = app.oneshot(Request::builder().uri("/healthz").body(Body::empty()).unwrap()).await.unwrap();
    let id = resp.headers().get("x-request-id").unwrap().to_str().unwrap();
    assert!(id.starts_with("req-"), "unexpected request id: {id}");
}

#[tokio::test]
async fn empty_input_yields_empty_manifest() {
    let app = build_router(test_state());
    let resp = app.oneshot(post("/v1/chunk", Body::from(Vec::<u8>::new()))).await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = json_body(resp).await;
    assert_eq!(body["total_len"], 0);
    assert_eq!(body["chunks"], json!([]));
    assert_eq!(
        body["content_sha256"],
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    );
    assert_eq!(body["algorithm"]["name"], "gear-cdc");
}

#[tokio::test]
async fn chunk_then_verify_accepts() {
    let app = build_router(test_state());
    let data = SplitMix(0x5555_0001).bytes(50_000);

    let resp = app.clone().oneshot(post("/v1/chunk", Body::from(data.clone()))).await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let manifest = json_body(resp).await;
    assert!(manifest["chunks"].as_array().unwrap().len() > 3);

    let verify_req = json!({
        "manifest": manifest,
        "content_base64": base64::engine::general_purpose::STANDARD.encode(&data),
        "reference_base64": base64::engine::general_purpose::STANDARD.encode(&data),
    });
    let resp = app
        .oneshot(post_json("/v1/verify", &verify_req))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = json_body(resp).await;
    assert_eq!(body["decision"], "accept");
    assert_eq!(body["byte_compare"], "identical");
    assert_eq!(body["total_len"], 50_000);
    assert!(body["request_id"].as_str().unwrap().starts_with("req-"));
}

#[tokio::test]
async fn verify_reports_chunk_digest_mismatch_category() {
    let app = build_router(test_state());
    let data = SplitMix(0x5555_0002).bytes(50_000);
    let resp = app.clone().oneshot(post("/v1/chunk", Body::from(data.clone()))).await.unwrap();
    let manifest = json_body(resp).await;

    let mut tampered = data.clone();
    tampered[10_000] ^= 0xFF;
    let verify_req = json!({
        "manifest": manifest,
        "content_base64": base64::engine::general_purpose::STANDARD.encode(&tampered),
    });
    let resp = app
        .oneshot(post_json("/v1/verify", &verify_req))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::UNPROCESSABLE_ENTITY);
    let body = json_body(resp).await;
    assert_eq!(body["error"]["category"], "chunk_digest_mismatch");
}

#[tokio::test]
async fn verify_byte_compare_catches_forged_digest() {
    let app = build_router(test_state());
    let data = SplitMix(0x5555_0003).bytes(50_000);
    let resp = app.clone().oneshot(post("/v1/chunk", Body::from(data.clone()))).await.unwrap();
    let mut manifest = json_body(resp).await;

    // Forge: tamper content AND rewrite the first chunk digest to match.
    let mut stored = data.clone();
    stored[0] ^= 0xFF;
    let first_len = manifest["chunks"][0]["len"].as_u64().unwrap() as usize;
    let forged = sha2::Sha256::digest(&stored[..first_len]);
    manifest["chunks"][0]["sha256"] = json!(hex::encode(forged));

    let verify_req = json!({
        "manifest": manifest,
        "content_base64": base64::engine::general_purpose::STANDARD.encode(&stored),
        "reference_base64": base64::engine::general_purpose::STANDARD.encode(&data),
    });
    let resp = app
        .oneshot(post_json("/v1/verify", &verify_req))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::UNPROCESSABLE_ENTITY);
    let body = json_body(resp).await;
    assert_eq!(body["error"]["category"], "byte_mismatch");
    assert!(body["error"]["message"].as_str().unwrap().contains("offset 0"));
}

#[tokio::test]
async fn oversized_body_is_rejected_with_category() {
    let state = AppState::new(
        ChunkParams { min_size: 256, avg_bits: 10, max_size: 4096 },
        Limits { max_body_bytes: 1024, ..Limits::default() },
    );
    let app = build_router(Arc::new(state));
    let big = vec![0u8; 2048];
    let resp = app.oneshot(post("/v1/chunk", Body::from(big))).await.unwrap();
    assert_eq!(resp.status(), StatusCode::PAYLOAD_TOO_LARGE);
    let body = json_body(resp).await;
    assert_eq!(body["error"]["category"], "body_too_large");
    assert!(body["request_id"].as_str().unwrap().starts_with("req-"));
}

#[tokio::test]
async fn binary_manifest_roundtrips_through_parse_endpoint() {
    let app = build_router(test_state());
    let data = SplitMix(0x5555_0004).bytes(30_000);

    let resp = app.clone().oneshot(post("/v1/chunk", Body::from(data))).await.unwrap();
    let json_manifest = json_body(resp).await;

    let resp = app
        .clone()
        .oneshot(post("/v1/chunk?format=binary", Body::from(SplitMix(0x5555_0004).bytes(30_000))))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    assert_eq!(
        resp.headers().get("content-type").unwrap(),
        "application/x-cdc-manifest"
    );
    let binary = axum::body::to_bytes(resp.into_body(), 1 << 20).await.unwrap();

    let resp = app.oneshot(post("/v1/manifest/parse", Body::from(binary.to_vec()))).await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let parsed = json_body(resp).await;
    assert_eq!(parsed, json_manifest);
}

#[tokio::test]
async fn corrupt_binary_manifest_is_rejected() {
    let app = build_router(test_state());
    let resp = app.clone().oneshot(post("/v1/chunk?format=binary", Body::from(vec![1u8; 5000]))).await.unwrap();
    let mut binary = axum::body::to_bytes(resp.into_body(), 1 << 20).await.unwrap().to_vec();
    let mid = binary.len() / 2;
    binary[mid] ^= 0x01;
    let resp = app.oneshot(post("/v1/manifest/parse", Body::from(binary))).await.unwrap();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = json_body(resp).await;
    assert_eq!(body["error"]["category"], "malformed_manifest");
}

#[tokio::test]
async fn verify_rejects_invalid_base64() {
    let app = build_router(test_state());
    let verify_req = json!({
        "manifest": { "algorithm": {"name":"gear-cdc","version":1,"min_size":256,"avg_bits":10,"max_size":4096,"mask_hex":"0x3ff","chunk_digest":"sha256"},
                      "total_len": 0, "content_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "chunks": [] },
        "content_base64": "!!!not-base64!!!",
    });
    let resp = app
        .oneshot(post_json("/v1/verify", &verify_req))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = json_body(resp).await;
    assert_eq!(body["error"]["category"], "invalid_base64");
}
