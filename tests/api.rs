//! API tests: exercise the axum diagnostic interface end-to-end (in-process,
//! no network), asserting request-identity correlation, typed error bodies,
//! persistence and the human-readable report.

use axum::body::{to_bytes, Body};
use axum::http::{Request, StatusCode};
use merge_checker::config::Config;
use merge_checker::routes::{self, AppState};
use merge_checker::store::RunStore;
use serde_json::{json, Value};
use std::path::PathBuf;
use std::sync::Arc;
use tower::ServiceExt;

fn manifest() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn test_state() -> (AppState, tempfile::TempDir) {
    let tmp = tempfile::tempdir().expect("tempdir");
    let config = Config {
        listen: "127.0.0.1:0".to_string(),
        workspace_root: manifest().canonicalize().unwrap(),
        store_path: tmp.path().join("runs.jsonl"),
    };
    let store = RunStore::open(&config.store_path).expect("open store");
    (
        AppState {
            config: Arc::new(config),
            store: Arc::new(store),
        },
        tmp,
    )
}

async fn call(app: axum::Router, req: Request<Body>) -> (StatusCode, axum::http::HeaderMap, Value) {
    let resp = app.oneshot(req).await.expect("response");
    let status = resp.status();
    let headers = resp.headers().clone();
    let bytes = to_bytes(resp.into_body(), 1 << 20).await.expect("body");
    let body: Value = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
    (status, headers, body)
}

fn output_dir(tag: &str) -> PathBuf {
    manifest().join(format!("target/test-outputs/{tag}-{}", uuid::Uuid::new_v4()))
}

#[tokio::test]
async fn health_reports_version() {
    let (state, _tmp) = test_state();
    let app = routes::router(state);
    let (status, _h, body) = call(
        app,
        Request::builder()
            .uri("/v1/health")
            .body(Body::empty())
            .unwrap(),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["name"], "oci-layer-merge-checker");
    assert_eq!(body["version"], merge_checker::model::TOOL_VERSION);
}

#[tokio::test]
async fn post_merge_runs_persists_and_correlates_request_id() {
    let (state, _tmp) = test_state();
    let store = state.store.clone();
    let app = routes::router(state);
    let out = output_dir("api-good");

    let body = json!({
        "layers": ["fixtures/layers/layer1", "fixtures/layers/layer2", "fixtures/layers/layer3"],
        "output_dir": out.to_string_lossy(),
    });
    let req = Request::builder()
        .method("POST")
        .uri("/v1/merges")
        .header("content-type", "application/json")
        .header("x-request-id", "req-api-test-1")
        .body(Body::from(body.to_string()))
        .unwrap();
    let (status, headers, body) = call(app, req).await;

    assert_eq!(status, StatusCode::OK, "body: {body}");
    // Request identity is correlated: echoed in header and embedded in run.
    assert_eq!(headers.get("x-request-id").unwrap(), "req-api-test-1");
    assert_eq!(body["request_id"], "req-api-test-1");
    assert_eq!(body["status"], "success");
    assert_eq!(body["tool_version"], merge_checker::model::TOOL_VERSION);
    assert!(body["run_id"].as_str().unwrap().starts_with("run-"));
    assert!(body["steps"].as_array().unwrap().len() >= 3);
    assert!(body["entries"].as_array().unwrap().iter().any(|e| e["path"] == "etc/app.conf"));

    // The produced tree is on disk with the expected content.
    assert_eq!(
        std::fs::read_to_string(out.join("etc/app.conf")).unwrap(),
        "app-version=3\n"
    );

    // Persisted: retrievable by run id, identical content.
    let run_id = body["run_id"].as_str().unwrap().to_string();
    let persisted = store.get(&run_id).expect("store read").expect("run persisted");
    assert_eq!(persisted.request_id, "req-api-test-1");
    let _ = std::fs::remove_dir_all(&out);
}

#[tokio::test]
async fn get_merge_returns_record_and_report() {
    let (state, _tmp) = test_state();
    let app = routes::router(state.clone());
    let out = output_dir("api-get");
    let run = routes::run_merge_for_test(
        state,
        vec![manifest().join("fixtures/layers/layer1")],
        out.clone(),
    )
    .await;

    let (status, _h, body) = call(
        app.clone(),
        Request::builder()
            .uri(format!("/v1/merges/{}", run.run_id))
            .body(Body::empty())
            .unwrap(),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["run_id"], run.run_id.as_str());

    // Human-readable report mentions key facts.
    let resp = app
        .oneshot(
            Request::builder()
                .uri(format!("/v1/merges/{}/report", run.run_id))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("response");
    assert_eq!(resp.status(), StatusCode::OK);
    let bytes = to_bytes(resp.into_body(), 1 << 20).await.unwrap();
    let text = String::from_utf8(bytes.to_vec()).unwrap();
    assert!(text.contains(&run.run_id));
    assert!(text.contains("layer[0]"));
    assert!(text.contains("entries:"));
    let _ = std::fs::remove_dir_all(&out);
}

#[tokio::test]
async fn unknown_run_is_404_with_typed_error() {
    let (state, _tmp) = test_state();
    let app = routes::router(state);
    let (status, _h, body) = call(
        app,
        Request::builder()
            .uri("/v1/merges/run-does-not-exist")
            .body(Body::empty())
            .unwrap(),
    )
    .await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["error"]["category"], "run_not_found");
}

#[tokio::test]
async fn layer_outside_workspace_is_rejected_with_category() {
    let (state, _tmp) = test_state();
    let app = routes::router(state);
    let body = json!({
        "layers": ["/etc"],
        "output_dir": "target/test-outputs/never-created",
    });
    let req = Request::builder()
        .method("POST")
        .uri("/v1/merges")
        .header("content-type", "application/json")
        .body(Body::from(body.to_string()))
        .unwrap();
    let (status, _h, body) = call(app, req).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"]["category"], "outside_workspace");
    assert!(body["request_id"].as_str().unwrap().starts_with("req-"));
}

#[tokio::test]
async fn path_traversal_in_request_is_contained() {
    let (state, _tmp) = test_state();
    let app = routes::router(state);
    // "../.." from the repo root escapes the workspace.
    let body = json!({
        "layers": ["fixtures/../../etc"],
        "output_dir": "target/test-outputs/never-created-2",
    });
    let req = Request::builder()
        .method("POST")
        .uri("/v1/merges")
        .header("content-type", "application/json")
        .body(Body::from(body.to_string()))
        .unwrap();
    let (status, _h, body) = call(app, req).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"]["category"], "outside_workspace");
}

#[tokio::test]
async fn malicious_merge_via_api_reports_uncertainties() {
    let (state, _tmp) = test_state();
    let app = routes::router(state);
    let out = output_dir("api-malicious");
    let body = json!({
        "layers": ["fixtures/malicious/layer-escape"],
        "output_dir": out.to_string_lossy(),
    });
    let req = Request::builder()
        .method("POST")
        .uri("/v1/merges")
        .header("content-type", "application/json")
        .body(Body::from(body.to_string()))
        .unwrap();
    let (status, _h, body) = call(app, req).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["status"], "with_uncertainties");
    assert_eq!(
        body["uncertainties"][0]["reason"],
        "symlink_target_outside_root"
    );
    assert!(!out.join("bad/evil").exists());
    let _ = std::fs::remove_dir_all(&out);
}
