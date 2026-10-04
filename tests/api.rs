//! HTTP interop tests against a real Axum server on an ephemeral port.
//! Asserts concrete response bodies, statuses, and error categories —
//! failures must map to explicit categories, never a blanket 200.

mod common;

use common::TestLog;
use rbp_column::config::AppConfig;
use rbp_column::server::{build_router, AppState};

/// Start the service on 127.0.0.1:0 and return its base URL.
async fn spawn_server() -> String {
    let mut cfg = AppConfig::default();
    cfg.server.host = "127.0.0.1".into();
    cfg.server.port = 0;
    let app = build_router(AppState::new(cfg));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind ephemeral port");
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        axum::serve(listener, app).await.expect("serve");
    });
    format!("http://{addr}")
}

#[tokio::test]
async fn version_and_health_endpoints() {
    let mut log = TestLog::new("api:version-health");
    let base = spawn_server().await;
    let client = reqwest::Client::new();

    let resp = client.get(format!("{base}/healthz")).send().await.unwrap();
    assert_eq!(resp.status(), 200);
    let body: serde_json::Value = resp.json().await.unwrap();
    assert_eq!(body["status"], "ok");
    log.step("healthz -> 200 {status: ok}");

    let resp = client.get(format!("{base}/version")).send().await.unwrap();
    assert_eq!(resp.status(), 200);
    let body: serde_json::Value = resp.json().await.unwrap();
    assert_eq!(body["format_version"], 1);
    assert!(body["crate_version"].as_str().unwrap().contains('.'));
    assert!(body["rustc"].as_str().unwrap().contains("rustc"));
    log.step(&format!("version -> {body}"));
    log.pass("health and version endpoints report real versions");
}

#[tokio::test]
async fn encode_then_decode_roundtrip_over_http() {
    let mut log = TestLog::new("api:roundtrip");
    let base = spawn_server().await;
    let client = reqwest::Client::new();

    let mut values: Vec<u64> = vec![7; 8];
    values.extend([1, 2, 3]);
    values.extend(vec![u64::MAX; 8]);
    log.step(&format!("input_values={} values={:?}", values.len(), values));

    let resp = client
        .post(format!("{base}/v1/encode"))
        .json(&serde_json::json!({"values": values}))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200);
    let enc: serde_json::Value = resp.json().await.unwrap();
    let hex_str = enc["hex"].as_str().unwrap();
    let run_id = enc["run_id"].as_str().unwrap().to_string();
    assert_eq!(enc["stats"]["rle_blocks"], 2);
    assert_eq!(enc["stats"]["bitpack_blocks"], 1);
    // Reported size must match the actual payload (regression: magic was
    // once double-counted).
    assert_eq!(
        enc["stats"]["output_bytes"].as_u64().unwrap() as usize,
        hex_str.len() / 2,
        "stats.output_bytes must equal the real encoded length"
    );
    log.step(&format!(
        "encode run_id={} hex_len={} stats={}",
        run_id,
        hex_str.len(),
        enc["stats"]
    ));

    let resp = client
        .post(format!("{base}/v1/decode"))
        .json(&serde_json::json!({"hex": hex_str}))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200);
    let dec: serde_json::Value = resp.json().await.unwrap();
    let decoded: Vec<u64> = serde_json::from_value(dec["values"].clone()).unwrap();
    assert_eq!(decoded, values, "HTTP roundtrip must be value-exact");
    log.step(&format!("decode run_id={} values={}", dec["run_id"], decoded.len()));
    log.pass("value-exact roundtrip through HTTP API");
}

#[tokio::test]
async fn encode_empty_column_is_rejected() {
    let mut log = TestLog::new("api:encode-empty");
    let base = spawn_server().await;
    let client = reqwest::Client::new();

    let resp = client
        .post(format!("{base}/v1/encode"))
        .json(&serde_json::json!({"values": []}))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 422);
    let body: serde_json::Value = resp.json().await.unwrap();
    assert_eq!(body["error"]["category"], "empty_input");
    assert!(body["run_id"].as_str().unwrap().starts_with("boot-"));
    log.step(&format!("response 422 body={body}"));
    log.pass("empty column -> 422 empty_input, not a fake success");
}

#[tokio::test]
async fn decode_invalid_hex_is_a_client_error() {
    let mut log = TestLog::new("api:decode-bad-hex");
    let base = spawn_server().await;
    let client = reqwest::Client::new();

    let resp = client
        .post(format!("{base}/v1/decode"))
        .json(&serde_json::json!({"hex": "zz-not-hex"}))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 400);
    let body: serde_json::Value = resp.json().await.unwrap();
    assert!(body["error"]["message"]
        .as_str()
        .unwrap()
        .contains("not valid hex"));
    log.step(&format!("response 400 body={body}"));
    log.pass("invalid hex -> 400 with explanatory message");
}

#[tokio::test]
async fn decode_corrupted_header_reports_location() {
    let mut log = TestLog::new("api:decode-corrupt-header");
    let base = spawn_server().await;
    let client = reqwest::Client::new();

    // magic + one header with illegal mode tag 9.
    let mut bytes = b"RBP1".to_vec();
    bytes.extend_from_slice(&[9, 8, 0, 0, 8, 0, 0, 0]);
    bytes.extend_from_slice(&[0u8; 8]);
    let resp = client
        .post(format!("{base}/v1/decode"))
        .json(&serde_json::json!({"hex": hex::encode(&bytes)}))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 422);
    let body: serde_json::Value = resp.json().await.unwrap();
    assert_eq!(body["error"]["category"], "invalid_mode");
    assert_eq!(body["error"]["block_index"], 0);
    assert_eq!(body["error"]["offset"], 4);
    log.step(&format!("response 422 body={body}"));
    log.pass("corrupted header -> 422 invalid_mode with block_index/offset");
}

#[tokio::test]
async fn decode_bad_magic_is_not_success() {
    let mut log = TestLog::new("api:decode-bad-magic");
    let base = spawn_server().await;
    let client = reqwest::Client::new();

    let resp = client
        .post(format!("{base}/v1/decode"))
        .json(&serde_json::json!({"hex": hex::encode(b"XXXX01234567")}))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 422);
    let body: serde_json::Value = resp.json().await.unwrap();
    assert_eq!(body["error"]["category"], "bad_magic");
    log.step(&format!("response 422 body={body}"));
    log.pass("bad magic -> 422 bad_magic, never a 200");
}
