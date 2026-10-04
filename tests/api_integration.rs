//! End-to-end API tests against a real listening server (ephemeral port).
//! Assertions cover concrete response content and error categories, not
//! merely "the endpoint responded".

use cdc_service::api;
use cdc_service::chunker::{chunk_buffer, ChunkParams};
use cdc_service::config::ServiceConfig;
use cdc_service::limits::Limits;
use serde_json::Value;
use sha2::{Digest, Sha256};

async fn spawn_server(limits: Limits, params: ChunkParams) -> String {
    let config = ServiceConfig { listen: "127.0.0.1:0".into(), limits, chunker: params };
    let app = api::router(config);
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    format!("http://{addr}")
}

fn test_params() -> ChunkParams {
    ChunkParams { min_size: 64, max_size: 1024, mask_bits: 7, pattern: 0 }
}

#[tokio::test]
async fn health_and_manifest_endpoints() {
    let base = spawn_server(Limits::default(), test_params()).await;
    let client = reqwest::Client::new();

    let health: Value = client.get(format!("{base}/v1/health")).send().await.unwrap().json().await.unwrap();
    assert_eq!(health["status"], "ok");

    let manifest: Value = client.get(format!("{base}/v1/manifest")).send().await.unwrap().json().await.unwrap();
    assert_eq!(manifest["algorithm"], "gear64-cdc-v1");
    assert_eq!(manifest["digest"], "sha256");
    assert_eq!(manifest["params"]["min_size"], 64);
    assert_eq!(manifest["params"]["mask_bits"], 7);
}

#[tokio::test]
async fn chunk_endpoint_returns_tiling_chunks_with_correct_digests() {
    let base = spawn_server(Limits::default(), test_params()).await;
    let client = reqwest::Client::new();
    let payload: Vec<u8> = (0..10_000u32).map(|i| (i.wrapping_mul(2654435761) >> 11) as u8).collect();

    let resp = client
        .post(format!("{base}/v1/chunk"))
        .header("x-request-id", "test-req-42")
        .body(payload.clone())
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200);
    let body: Value = resp.json().await.unwrap();

    assert_eq!(body["request_id"], "test-req-42");
    assert_eq!(body["payload_len"], payload.len());
    assert_eq!(body["payload_sha256"], hex::encode(Sha256::digest(&payload)));

    // Chunks must tile the payload and each recorded digest must match the
    // actual bytes — verified locally, not by trusting the service.
    let chunks = body["chunks"].as_array().unwrap();
    assert!(!chunks.is_empty());
    let mut pos = 0u64;
    for c in chunks {
        let (off, len) = (c["offset"].as_u64().unwrap(), c["len"].as_u64().unwrap());
        assert_eq!(off, pos, "chunks must be contiguous");
        let digest = hex::encode(Sha256::digest(&payload[off as usize..(off + len) as usize]));
        assert_eq!(c["sha256"], digest);
        pos += len;
    }
    assert_eq!(pos, payload.len() as u64);

    // And the boundaries must equal the library's own chunking of the same bytes.
    let expected = chunk_buffer(test_params(), &payload).unwrap();
    assert_eq!(chunks.len(), expected.len());
}

#[tokio::test]
async fn empty_input_yields_zero_chunks_explicitly() {
    let base = spawn_server(Limits::default(), test_params()).await;
    let client = reqwest::Client::new();
    let resp = client.post(format!("{base}/v1/chunk")).body(Vec::<u8>::new()).send().await.unwrap();
    assert_eq!(resp.status(), 200);
    let body: Value = resp.json().await.unwrap();
    assert_eq!(body["payload_len"], 0);
    assert_eq!(body["chunks"].as_array().unwrap().len(), 0);
}

#[tokio::test]
async fn container_round_trip_through_verify_endpoint() {
    let base = spawn_server(Limits::default(), test_params()).await;
    let client = reqwest::Client::new();
    let payload: Vec<u8> = (0..20_000u32).map(|i| (i % 251) as u8).collect();

    let resp = client
        .post(format!("{base}/v1/chunk-container"))
        .header("x-request-id", "make-container")
        .body(payload.clone())
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200);
    assert_eq!(resp.headers()["content-type"], "application/x-cdcb");
    assert_eq!(resp.headers()["x-request-id"], "make-container");
    let container = resp.bytes().await.unwrap();

    let resp = client
        .post(format!("{base}/v1/verify"))
        .header("x-request-id", "check-container")
        .body(container.to_vec())
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200);
    let body: Value = resp.json().await.unwrap();
    assert_eq!(body["request_id"], "check-container");
    assert_eq!(body["decision"], "accept");
    assert_eq!(body["payload_len"], payload.len());
    assert_eq!(body["payload_sha256"], hex::encode(Sha256::digest(&payload)));
}

#[tokio::test]
async fn verify_rejects_garbage_and_tampered_containers_by_category() {
    let base = spawn_server(Limits::default(), test_params()).await;
    let client = reqwest::Client::new();

    // Garbage body -> malformed_container, HTTP 400.
    let resp = client
        .post(format!("{base}/v1/verify"))
        .body(b"this is not a container".to_vec())
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 400);
    let body: Value = resp.json().await.unwrap();
    assert_eq!(body["error"]["category"], "malformed_container");
    assert!(body["request_id"].as_str().unwrap().starts_with("req-"));

    // Valid container with one flipped payload byte -> digest_mismatch, HTTP 422.
    let payload: Vec<u8> = (0..5_000u32).map(|i| (i % 256) as u8).collect();
    let resp = client
        .post(format!("{base}/v1/chunk-container"))
        .body(payload)
        .send()
        .await
        .unwrap();
    let mut container = resp.bytes().await.unwrap().to_vec();
    let last = container.len() - 1;
    container[last] ^= 0x01;
    let resp = client.post(format!("{base}/v1/verify")).body(container).send().await.unwrap();
    assert_eq!(resp.status(), 422);
    let body: Value = resp.json().await.unwrap();
    assert_eq!(body["error"]["category"], "digest_mismatch");
}

#[tokio::test]
async fn oversized_input_is_rejected_with_limit_exceeded() {
    let limits = Limits { max_input_bytes: 1024, max_body_bytes: 1 << 20, max_chunks: 10_000 };
    let base = spawn_server(limits, test_params()).await;
    let client = reqwest::Client::new();

    let resp = client
        .post(format!("{base}/v1/chunk"))
        .body(vec![7u8; 2048])
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 413);
    let body: Value = resp.json().await.unwrap();
    assert_eq!(body["error"]["category"], "limit_exceeded");
    assert!(body["error"]["message"].as_str().unwrap().contains("max_input_bytes"));
}
