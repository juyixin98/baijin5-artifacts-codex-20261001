//! Persistence round-trip and diagnostics API tests, including error
//! categories for missing/invalid resources.

mod common;

use cfs_sim::diag::{self, AppState};
use cfs_sim::persist::{RunStore, StoreError};
use cfs_sim::scenario::find_builtin;
use common::log_step;
use std::sync::Arc;

const CASE: &str = "persist-api";

#[test]
fn store_roundtrip_preserves_report_and_samples() {
    let dir = tempfile::tempdir().unwrap();
    let store = RunStore::new(dir.path()).unwrap();
    let scenario = find_builtin("periodic-sleeper").unwrap();
    let (run_id, report) = diag::execute_scenario(&store, &scenario).unwrap();

    let loaded = store.load_report(&run_id).unwrap();
    log_step(CASE, "roundtrip-run-id", &run_id, &loaded.run_id,
        "report persists under its run id", loaded.run_id == run_id);
    assert_eq!(loaded.run_id, run_id);
    assert_eq!(loaded.elapsed_ms, report.elapsed_ms);
    assert_eq!(loaded.busy_ms, report.busy_ms);
    assert_eq!(loaded.idle_ms, report.idle_ms);
    assert_eq!(loaded.tasks.len(), report.tasks.len());
    assert_eq!(loaded.checks.len(), report.checks.len());
    assert!(loaded.passed);

    let samples = store.load_samples(&run_id).unwrap();
    log_step(CASE, "samples-persisted", "> 0", &samples.len().to_string(),
        "samples.jsonl holds one JSON state per line", !samples.is_empty());
    assert!(!samples.is_empty());
    let final_idle = samples.last().unwrap().idle_ms;
    common::check_eq(CASE, "final-sample-idle", 500, final_idle,
        "hand-computed: sleeper idles the CPU for 100*5ms");

    let listed = store.list().unwrap();
    let found = listed.iter().any(|r| r.run_id == run_id && r.passed);
    log_step(CASE, "list-contains-run", "true", &found.to_string(),
        "saved runs appear in the listing", found);
    assert!(found);
}

#[test]
fn store_errors_are_categorized() {
    let dir = tempfile::tempdir().unwrap();
    let store = RunStore::new(dir.path()).unwrap();

    let missing = store.load_report("run-does-not-exist").unwrap_err();
    let is_not_found = matches!(missing, StoreError::NotFound { .. });
    log_step(CASE, "missing-run", "NotFound", &format!("{missing:?}"),
        "absent run id maps to NotFound, not a generic io error", is_not_found);
    assert!(is_not_found);

    let traversal = store.load_report("../outside").unwrap_err();
    let is_invalid = matches!(traversal, StoreError::InvalidRunId(_));
    log_step(CASE, "traversal-run-id", "InvalidRunId", &format!("{traversal:?}"),
        "path separators in run ids are rejected before touching the fs",
        is_invalid);
    assert!(is_invalid);
}

mod api {
    use super::*;
    use axum::body::{to_bytes, Body};
    use axum::http::{Request, StatusCode};
    use tower::ServiceExt;

    fn app() -> (axum::Router, tempfile::TempDir) {
        let dir = tempfile::tempdir().unwrap();
        let store = RunStore::new(dir.path()).unwrap();
        (diag::router(Arc::new(AppState { store })), dir)
    }

    async fn json_body(resp: axum::response::Response) -> serde_json::Value {
        let bytes = to_bytes(resp.into_body(), 1 << 20).await.unwrap();
        serde_json::from_slice(&bytes).unwrap()
    }

    #[tokio::test]
    async fn happy_path_run_lifecycle() {
        let (app, _dir) = app();

        let resp = app
            .clone()
            .oneshot(Request::builder().uri("/healthz").body(Body::empty()).unwrap())
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let body = json_body(resp).await;
        log_step(CASE, "healthz", "ok+version", &body.to_string(),
            "liveness reports the binary version",
            body["status"] == "ok" && body["version"].is_string());
        assert_eq!(body["status"], "ok");
        assert_eq!(body["version"], cfs_sim::VERSION);

        let resp = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/api/runs")
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"scenario":"idle-trace"}"#))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::CREATED);
        let body = json_body(resp).await;
        let run_id = body["run_id"].as_str().unwrap().to_string();
        log_step(CASE, "create-run", "201+passed", &body.to_string(),
            "idle-trace checks all pass", body["passed"] == true);
        assert_eq!(body["passed"], true);

        let resp = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri(format!("/api/runs/{run_id}"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let body = json_body(resp).await;
        log_step(CASE, "get-run", "idle=100 busy=50",
            &format!("idle={} busy={}", body["idle_ms"], body["busy_ms"]),
            "hand-computed idle-trace totals",
            body["idle_ms"] == 100 && body["busy_ms"] == 50);
        assert_eq!(body["idle_ms"], 100);
        assert_eq!(body["busy_ms"], 50);

        let resp = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri(format!("/api/runs/{run_id}/samples"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let body = json_body(resp).await;
        assert!(body.as_array().unwrap().len() > 100);

        let resp = app
            .oneshot(Request::builder().uri("/api/runs").body(Body::empty()).unwrap())
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let body = json_body(resp).await;
        let listed = body
            .as_array()
            .unwrap()
            .iter()
            .any(|r| r["run_id"] == run_id);
        log_step(CASE, "list-runs", "contains new run", &body.to_string(),
            "created runs are listed", listed);
        assert!(listed);
    }

    #[tokio::test]
    async fn error_categories_are_distinct_and_never_success() {
        let (app, _dir) = app();

        // Unknown scenario -> 400 unknown_scenario
        let resp = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/api/runs")
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"scenario":"nope"}"#))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
        let body = json_body(resp).await;
        log_step(CASE, "unknown-scenario", "400/unknown_scenario", &body.to_string(),
            "unknown builtin name is a client error with its own kind",
            body["error"]["kind"] == "unknown_scenario");
        assert_eq!(body["error"]["kind"], "unknown_scenario");

        // Missing both fields -> 400 invalid_request
        let resp = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/api/runs")
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{}"#))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
        let body = json_body(resp).await;
        assert_eq!(body["error"]["kind"], "invalid_request");

        // Inline spec with weight 0 -> 400 invalid_scenario
        let resp = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/api/runs")
                    .header("content-type", "application/json")
                    .body(Body::from(
                        r#"{"spec":{"name":"bad","duration_ms":10,"tasks":[{"name":"t","weight":0,"script":[{"type":"run","ms":1}],"repeats":1}]}}"#,
                    ))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
        let body = json_body(resp).await;
        log_step(CASE, "invalid-spec", "400/invalid_scenario", &body.to_string(),
            "spec validation failures are client errors",
            body["error"]["kind"] == "invalid_scenario");
        assert_eq!(body["error"]["kind"], "invalid_scenario");

        // Missing run -> 404 not_found
        let resp = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/api/runs/run-does-not-exist")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
        let body = json_body(resp).await;
        assert_eq!(body["error"]["kind"], "not_found");

        // Path-traversal-ish run id -> 4xx, never 200 (never a fs read).
        // Depending on where the encoded slash is rejected (router or the
        // store's id validation) the body may be empty, so only the status
        // class is asserted.
        let resp = app
            .oneshot(
                Request::builder()
                    .uri("/api/runs/..%2F..%2Fetc")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        let status = resp.status();
        let bytes = to_bytes(resp.into_body(), 1 << 20).await.unwrap();
        let text = String::from_utf8_lossy(&bytes).to_string();
        log_step(CASE, "traversal-run-id", "4xx, never 200",
            &format!("status={status} body={text}"),
            "ids with path separators are rejected before any fs access",
            status.is_client_error());
        assert!(status.is_client_error());
    }
}
