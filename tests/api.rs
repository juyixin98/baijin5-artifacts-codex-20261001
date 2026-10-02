//! End-to-end API test: ingest a fixture through HTTP, then query deltas,
//! tree and diagnostics. Diagnostics must carry request ids and must not
//! leak raw process command names (only redacted hashes).

mod common;

use common::*;
use axum::body::Body;
use axum::http::{Request, StatusCode};
use procdiff::api::{router, AppState};
use procdiff::engine::Engine;
use procdiff::store::Store;
use std::sync::{Arc, Mutex};
use tower::ServiceExt;

fn test_app(data_dir: std::path::PathBuf) -> axum::Router {
    let engine = Engine::new(test_config());
    router(Arc::new(AppState {
        engine: Mutex::new(engine),
        store: Store::new(data_dir),
    }))
}

async fn json_body(resp: axum::response::Response) -> serde_json::Value {
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .expect("body bytes");
    serde_json::from_slice(&bytes).expect("json body")
}

#[tokio::test]
async fn ingest_query_and_diagnose_via_http() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let app = test_app(tmp.path().to_path_buf());

    // Ingest the whole pid_reuse fixture root.
    let ingest_req = Request::post("/v1/snapshots/ingest")
        .header("content-type", "application/json")
        .body(Body::from(
            serde_json::json!({"path": fixtures_dir().join("pid_reuse").to_string_lossy()})
                .to_string(),
        ))
        .unwrap();
    let resp = app.clone().oneshot(ingest_req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = json_body(resp).await;
    let reports = body["reports"].as_array().unwrap();
    assert_eq!(reports.len(), 5);
    assert!(reports.iter().all(|r| !r["rejected"].as_bool().unwrap()));
    assert_eq!(reports[0]["request_id"], "req-000001");

    // Deltas for the reused pid: reset boundary visible over HTTP.
    let resp = app
        .clone()
        .oneshot(
            Request::get("/v1/processes/2000/deltas")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let body = json_body(resp).await;
    let deltas = body["deltas"].as_array().unwrap();
    assert_eq!(deltas.len(), 4);
    assert_eq!(deltas[0]["class"], "ok");
    assert_eq!(deltas[0]["cpu_jiffies"], 15);
    assert_eq!(deltas[2]["class"], "reset");
    assert!(deltas[2]["cpu_jiffies"].is_null());
    assert_eq!(deltas[3]["cpu_jiffies"], 10);

    // Tree at seq 4: gen2 (start=900) is the live pid 2000, parented to init.
    let resp = app
        .clone()
        .oneshot(Request::get("/v1/tree?seq=4").body(Body::empty()).unwrap())
        .await
        .unwrap();
    let body = json_body(resp).await;
    let nodes = body["nodes"].as_array().unwrap();
    let gen2 = nodes
        .iter()
        .find(|n| n["identity"]["pid"] == 2000)
        .expect("pid 2000 node");
    assert_eq!(gen2["identity"]["start_time"], 900);
    assert_eq!(gen2["ppid"], 1);

    // Diagnostics: request ids present, decisions explained, comm redacted.
    let resp = app
        .clone()
        .oneshot(Request::get("/v1/diagnostics").body(Body::empty()).unwrap())
        .await
        .unwrap();
    let body = json_body(resp).await;
    let text = body.to_string();
    assert!(text.contains("req-"));
    assert!(text.contains("generation-reset"));
    assert!(!text.contains("worker"), "raw comm leaked: {text}");
    assert!(text.contains("comm#"), "redacted hash missing: {text}");

    // State was persisted.
    assert!(tmp.path().join("state.json").exists());
}

#[tokio::test]
async fn ingest_of_missing_path_is_a_clean_400() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let app = test_app(tmp.path().to_path_buf());
    let req = Request::post("/v1/snapshots/ingest")
        .header("content-type", "application/json")
        .body(Body::from(
            serde_json::json!({"path": "/nonexistent/snapshots"}).to_string(),
        ))
        .unwrap();
    let resp = app.oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = json_body(resp).await;
    assert!(body["error"].as_str().unwrap().contains("does not exist"));
}
