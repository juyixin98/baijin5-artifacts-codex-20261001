//! End-to-end API tests: a real server bound to an ephemeral localhost
//! port, exercised with a real HTTP client. Covers the happy path
//! (request-id correlation, provenance, diagnostics endpoints) and the
//! failure categories (outside-roots layer, missing layer, empty request,
//! unknown/invalid run id).

use merge_check::api::build_router;
use merge_check::config::AppConfig;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};

fn fixtures_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures")
}

async fn start_server() -> (String, tempfile::TempDir) {
    let state_tmp = tempfile::tempdir().unwrap();
    let config = AppConfig {
        bind: "127.0.0.1:0".to_string(),
        state_dir: state_tmp.path().to_path_buf(),
        layer_roots: vec![fixtures_dir().canonicalize().unwrap()],
        max_entries: 10_000,
        max_symlink_depth: 8,
    };
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        axum::serve(listener, build_router(config)).await.unwrap();
    });
    (format!("http://{addr}"), state_tmp)
}

fn layer_path(scenario: &str, layer: &str) -> String {
    fixtures_dir()
        .join(scenario)
        .join(layer)
        .to_string_lossy()
        .into_owned()
}

#[tokio::test]
async fn health_and_version() {
    let (base, _tmp) = start_server().await;
    let client = reqwest::Client::new();

    let health: Value = client
        .get(format!("{base}/v1/health"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(health["status"], "ok");

    let version: Value = client
        .get(format!("{base}/v1/version"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(version["engine_version"], merge_check::model::ENGINE_VERSION);
    assert_eq!(
        version["report_schema_version"],
        merge_check::model::REPORT_SCHEMA_VERSION
    );
}

#[tokio::test]
async fn merge_happy_path_correlates_request_id_and_provenance() {
    let (base, _tmp) = start_server().await;
    let client = reqwest::Client::new();

    let resp = client
        .post(format!("{base}/v1/merges"))
        .header("x-request-id", "req-happy-1")
        .json(&json!({
            "layers": [
                { "id": "base", "path": layer_path("delete-recreate", "layer1") },
                { "id": "update", "path": layer_path("delete-recreate", "layer2") }
            ]
        }))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 201);
    let created: Value = resp.json().await.unwrap();
    let run_id = created["run_id"].as_str().unwrap().to_string();
    assert_eq!(created["request_id"], "req-happy-1");
    assert_eq!(created["engine_version"], merge_check::model::ENGINE_VERSION);
    assert_eq!(created["entries"], 2);
    assert_eq!(created["failures"], 0);

    // Full report: provenance of the recreated file points at layer 1.
    let report: Value = client
        .get(format!("{base}/v1/merges/{run_id}"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(report["run_id"], run_id);
    assert_eq!(report["request_id"], "req-happy-1");
    let entry = &report["entries"]["app/config.txt"];
    assert_eq!(entry["source_layer"], 1);
    assert_eq!(entry["layer_id"], "update");
    assert_eq!(
        entry["sha256"],
        "ba38d1fa1f713cc6f8ee9e7bead514c246aaff315ab6e97f6b0caeada8849665"
    );

    // Entries endpoint returns the same tree as a list.
    let entries: Value = client
        .get(format!("{base}/v1/merges/{run_id}/entries"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(entries.as_array().unwrap().len(), 2);

    // Diagnostics endpoint separates failures from uncertainties.
    let diags: Value = client
        .get(format!("{base}/v1/merges/{run_id}/diagnostics"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(diags["failures"].as_array().unwrap().len(), 0);
    assert_eq!(diags["uncertainties"].as_array().unwrap().len(), 0);

    // List endpoint shows the run.
    let runs: Value = client
        .get(format!("{base}/v1/merges"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert!(runs
        .as_array()
        .unwrap()
        .iter()
        .any(|r| r["run_id"] == run_id));
}

#[tokio::test]
async fn merge_malicious_fixture_reports_failures_and_uncertainties() {
    let (base, _tmp) = start_server().await;
    let client = reqwest::Client::new();

    let resp = client
        .post(format!("{base}/v1/merges"))
        .json(&json!({ "layers": [layer_path("malicious", "layer1")] }))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 201);
    let created: Value = resp.json().await.unwrap();
    assert_eq!(created["failures"], 4);
    assert_eq!(created["uncertainties"], 1);
    let run_id = created["run_id"].as_str().unwrap();

    let diags: Value = client
        .get(format!("{base}/v1/merges/{run_id}/diagnostics"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let mut cats: Vec<&str> = diags["failures"]
        .as_array()
        .unwrap()
        .iter()
        .map(|d| d["category"].as_str().unwrap())
        .collect();
    cats.sort_unstable();
    assert_eq!(
        cats,
        ["absolute_target", "escapes_root", "link_loop", "link_loop"]
    );
    assert_eq!(diags["uncertainties"][0]["category"], "dangling_link");
    // Every diagnostic carries its layer index and path for explainability.
    for d in diags["failures"].as_array().unwrap() {
        assert!(d["layer"].is_number());
        assert!(d["path"].is_string());
        assert!(d["message"].is_string());
    }
}

#[tokio::test]
async fn layer_outside_roots_is_forbidden() {
    let (base, _tmp) = start_server().await;
    let client = reqwest::Client::new();

    let resp = client
        .post(format!("{base}/v1/merges"))
        .json(&json!({ "request_id": "req-escape", "layers": ["/etc"] }))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 403);
    let body: Value = resp.json().await.unwrap();
    assert_eq!(body["error"]["code"], "layer_outside_roots");
    assert_eq!(body["error"]["request_id"], "req-escape");
}

#[tokio::test]
async fn missing_layer_is_a_client_error() {
    let (base, _tmp) = start_server().await;
    let client = reqwest::Client::new();

    let resp = client
        .post(format!("{base}/v1/merges"))
        .json(&json!({ "layers": ["/definitely/not/a/layer"] }))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 400);
    let body: Value = resp.json().await.unwrap();
    assert_eq!(body["error"]["code"], "layer_unavailable");
}

#[tokio::test]
async fn empty_layer_list_is_rejected() {
    let (base, _tmp) = start_server().await;
    let client = reqwest::Client::new();

    let resp = client
        .post(format!("{base}/v1/merges"))
        .json(&json!({ "layers": [] }))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 400);
    let body: Value = resp.json().await.unwrap();
    assert_eq!(body["error"]["code"], "no_layers");
}

#[tokio::test]
async fn unknown_and_invalid_run_ids_are_distinct_errors() {
    let (base, _tmp) = start_server().await;
    let client = reqwest::Client::new();

    let resp = client
        .get(format!("{base}/v1/merges/00000000-0000-0000-0000-000000000000"))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 404);
    let body: Value = resp.json().await.unwrap();
    assert_eq!(body["error"]["code"], "run_not_found");

    let resp = client
        .get(format!("{base}/v1/merges/bad%20id"))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 400);
    let body: Value = resp.json().await.unwrap();
    assert_eq!(body["error"]["code"], "invalid_run_id");
}
