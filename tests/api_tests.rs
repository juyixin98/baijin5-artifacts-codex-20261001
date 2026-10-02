//! API-level tests: real HTTP round-trips through the axum router, asserting
//! concrete payloads and error *categories* (not just "endpoint responds").

mod common;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use blksched::api::{router, AppState};
use blksched::config::Config;
use blksched::state::RunStore;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use tower::ServiceExt;

static COUNTER: AtomicU64 = AtomicU64::new(0);

fn test_app() -> (axum::Router, String) {
    let n = COUNTER.fetch_add(1, Ordering::SeqCst);
    let data_dir = std::env::temp_dir().join(format!(
        "blksched-api-test-{}-{n}",
        std::process::id()
    ));
    let mut cfg = Config::default();
    cfg.fixtures_dir = format!("{}/fixtures/traces", env!("CARGO_MANIFEST_DIR"));
    cfg.data_dir = data_dir.to_string_lossy().into_owned();
    let store = RunStore::new(&cfg.data_dir).unwrap();
    let app = router(Arc::new(AppState { store, cfg }));
    (app, data_dir.to_string_lossy().into_owned())
}

async fn json_body(resp: axum::response::Response) -> serde_json::Value {
    let bytes = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    serde_json::from_slice(&bytes).unwrap()
}

fn post_json(path: &str, body: serde_json::Value) -> Request<Body> {
    Request::post(path)
        .header("content-type", "application/json")
        .body(Body::from(body.to_string()))
        .unwrap()
}

fn tiny_run_request() -> serde_json::Value {
    serde_json::json!({
        "trace": {"name": "tiny"},
        "scheduler": {"kind": "scan"},
        "device": {"kind": "hdd", "seek_base_ns": 0, "seek_ns_per_sector": 1, "transfer_ns_per_sector": 1}
    })
}

#[tokio::test]
async fn health_and_version_are_explainable() {
    let (app, _) = test_app();
    let resp = app
        .clone()
        .oneshot(Request::get("/v1/health").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    assert_eq!(json_body(resp).await["status"], "ok");

    let resp = app
        .oneshot(Request::get("/v1/version").body(Body::empty()).unwrap())
        .await
        .unwrap();
    let v = json_body(resp).await;
    assert!(v["version"].is_string());
    assert_eq!(v["event_schema"], 1);
}

#[tokio::test]
async fn tiny_run_returns_hand_computed_summary() {
    let (app, _) = test_app();
    let resp = app
        .oneshot(post_json("/v1/runs", tiny_run_request()))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::CREATED);
    let body = json_body(resp).await;
    assert_eq!(body["summary"]["makespan_ns"], 210);
    assert_eq!(body["summary"]["total_seek_distance_sectors"], 175);
    assert_eq!(body["summary"]["deadline_misses"], serde_json::json!(["r3"]));
    assert_eq!(body["summary"]["completed"], 4);
    // Caveat is surfaced, not hidden.
    assert!(body["summary"]["device_caveat"]
        .as_str()
        .unwrap()
        .contains("not measured"));
}

#[tokio::test]
async fn error_categories_are_machine_readable() {
    let (app, _) = test_app();

    // Unknown fixture -> 404 NOT_FOUND.
    let resp = app
        .clone()
        .oneshot(post_json(
            "/v1/runs",
            serde_json::json!({"trace": {"name": "does_not_exist"}, "scheduler": {"kind": "scan"}}),
        ))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    assert_eq!(json_body(resp).await["error"]["code"], "NOT_FOUND");

    // Zero-length request -> 400 VALIDATION_FAILED.
    let resp = app
        .clone()
        .oneshot(post_json(
            "/v1/runs",
            serde_json::json!({
                "trace": {"ops": [{"type": "arrive", "at_ns": 0, "id": "a", "start": 0, "len": 0, "direction": "read"}]},
                "scheduler": {"kind": "scan"}
            }),
        ))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    assert_eq!(json_body(resp).await["error"]["code"], "VALIDATION_FAILED");

    // Unknown scheduler kind -> 400.
    let resp = app
        .clone()
        .oneshot(post_json(
            "/v1/runs",
            serde_json::json!({"trace": {"name": "tiny"}, "scheduler": {"kind": "bogus"}}),
        ))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);

    // Unknown run id -> 404.
    let resp = app
        .oneshot(Request::get("/v1/runs/run-999999").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn compare_runs_both_schedulers_and_reports_deltas() {
    let (app, _) = test_app();
    let resp = app
        .oneshot(post_json(
            "/v1/runs/compare",
            serde_json::json!({
                "trace": {"name": "tiny"},
                "device": {"kind": "hdd", "seek_base_ns": 0, "seek_ns_per_sector": 1, "transfer_ns_per_sector": 1}
            }),
        ))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = json_body(resp).await;
    assert_ne!(body["deadline_run_id"], body["scan_run_id"]);
    assert_eq!(body["deadline"]["scheduler"], "deadline");
    assert_eq!(body["scan"]["scheduler"], "scan");
    assert!(body["comparison"]["notes"].as_array().unwrap().len() >= 3);
}

#[tokio::test]
async fn events_endpoint_correlates_request_identities() {
    let (app, _) = test_app();
    let resp = app
        .clone()
        .oneshot(post_json("/v1/runs", tiny_run_request()))
        .await
        .unwrap();
    let created = json_body(resp).await;
    let run_id = created["run_id"].as_str().unwrap().to_string();

    let resp = app
        .oneshot(
            Request::get(format!("/v1/runs/{run_id}/events"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = json_body(resp).await;
    let events = body["events"].as_array().unwrap();
    assert!(!events.is_empty());
    // Every event carries a timestamp and at least one request id.
    for e in events {
        assert!(e["t_ns"].is_u64());
        assert!(!e["request_ids"].as_array().unwrap().is_empty());
    }
    // A dispatch event names its scheduling reason.
    let dispatch = events
        .iter()
        .find(|e| e["kind"] == "dispatched")
        .expect("dispatch event");
    assert_eq!(dispatch["reason"]["type"], "scan_sweep");
}

#[tokio::test]
async fn runs_survive_restart_via_journal() {
    let (app, data_dir) = test_app();
    let resp = app
        .oneshot(post_json("/v1/runs", tiny_run_request()))
        .await
        .unwrap();
    let created = json_body(resp).await;
    let run_id = created["run_id"].as_str().unwrap().to_string();

    // New AppState (fresh memory) over the same data dir: journal reload.
    let mut cfg = Config::default();
    cfg.fixtures_dir = format!("{}/fixtures/traces", env!("CARGO_MANIFEST_DIR"));
    cfg.data_dir = data_dir;
    let store = RunStore::new(&cfg.data_dir).unwrap();
    let app2 = router(Arc::new(AppState { store, cfg }));
    let resp = app2
        .oneshot(
            Request::get(format!("/v1/runs/{run_id}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    assert_eq!(json_body(resp).await["makespan_ns"], 210);
}
