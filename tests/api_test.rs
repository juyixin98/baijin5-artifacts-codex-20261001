//! End-to-end HTTP tests driving the real Axum router with `tower::oneshot`.
//! These assert status codes, error categories and concrete values — not mere
//! reachability.

use axum::body::Body;
use axum::extract::ConnectInfo;
use axum::http::{Request, StatusCode};
use axum::Extension;
use groupagg::exec::EngineConfig;
use groupagg::server::{build_router, AppState};
use serde_json::{json, Value};
use std::net::{IpAddr, SocketAddr};
use tempfile::tempdir;
use tower::ServiceExt;

/// The handlers extract `ConnectInfo`, which `oneshot` does not provide; the
/// test router installs a fixed peer address the same way `serve` does.
fn test_router(state: AppState) -> axum::Router {
    let peer = SocketAddr::new(IpAddr::from([127, 0, 0, 1]), 4321);
    build_router(state).layer(Extension(ConnectInfo(peer)))
}

fn state() -> (AppState, tempfile::TempDir) {
    let dir = tempdir().unwrap();
    let state = AppState::new(EngineConfig {
        memory_budget_bytes: 2048,
        spill_root: dir.path().to_path_buf(),
        cancel_check_rows: 4,
    });
    (state, dir)
}

async fn post_json(router: &axum::Router, uri: &str, body: Value) -> (StatusCode, Value) {
    let resp = router
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(uri)
                .header("content-type", "application/json")
                .header("x-request-id", "test-request-1")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = resp.status();
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let value = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
    (status, value)
}

#[tokio::test]
async fn health_and_fixture_catalog() {
    let (state, _dir) = state();
    let app = test_router(state);
    let resp = app
        .oneshot(
            Request::builder()
                .uri("/health")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);

    let resp = build_router(AppState::new(EngineConfig {
        memory_budget_bytes: 1,
        spill_root: tempdir().unwrap().path().to_path_buf(),
        cancel_check_rows: 1,
    }))
    .oneshot(
        Request::builder()
            .uri("/fixtures")
            .body(Body::empty())
            .unwrap(),
    )
    .await
    .unwrap();
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&bytes).unwrap();
    let names: Vec<&str> = json["fixtures"]
        .as_array()
        .unwrap()
        .iter()
        .map(|f| f["name"].as_str().unwrap())
        .collect();
    assert!(names.contains(&"even_sample"));
    assert!(names.contains(&"large_repeat_group"));
}

#[tokio::test]
async fn fixture_query_returns_concrete_hand_computed_values() {
    let (state, _dir) = state();
    let app = test_router(state);
    let body = json!({
        "query_id": "http-even",
        "fixture": "even_sample",
        "plan": {
            "group_by": ["g"],
            "aggregations": [
                {"alias":"median","column":"score","op":"percentile_cont","quantile":0.5},
                {"alias":"joined","column":"tag","op":"string_agg","delimiter":"|"}
            ]
        }
    });
    let (status, json) = post_json(&app, "/query", body).await;
    assert_eq!(status, StatusCode::OK, "{json}");
    assert_eq!(json["request_id"], "test-request-1");
    assert_eq!(json["decision"]["verdict"], "accepted");
    let group = &json["data"]["groups"][0];
    assert_eq!(group["group"]["g"], "even");
    assert_eq!(group["aggregations"]["median"], 25.0);
    assert_eq!(group["aggregations"]["joined"], "a|b|c|d");
    assert!(json["data"]["stats"]["spill_runs"].as_u64().unwrap() >= 1);
    assert!(json["data"]["stats"]["peak_memory_bytes"].as_u64().unwrap() <= 2048);
}

#[tokio::test]
async fn out_of_range_quantile_is_rejected_with_category() {
    let (state, _dir) = state();
    let app = test_router(state);
    let body = json!({
        "query_id": "http-badq",
        "fixture": "even_sample",
        "plan": {
            "group_by": ["g"],
            "aggregations": [
                {"alias":"bad","column":"score","op":"percentile_cont","quantile":1.5}
            ]
        }
    });
    let (status, json) = post_json(&app, "/query", body).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(json["error_kind"], "INVALID_QUANTILE");
    assert_eq!(json["status"], "error");
    assert_eq!(json["decision"]["verdict"], "rejected");
    // Diagnostic explains the rejection and carries the request id.
    assert!(json["decision"]["reason"].as_str().unwrap().contains("1.5"));
}

#[tokio::test]
async fn type_mismatch_and_unknown_column_are_rejected() {
    let (state, _dir) = state();
    let app = test_router(state);

    // string_agg over a numeric column
    let body = json!({
        "query_id": "http-type",
        "schema": [{"name":"v","data_type":"int64"}],
        "columns": {"v": [1, 2, 3]},
        "plan": {"aggregations": [
            {"alias":"s","column":"v","op":"string_agg","delimiter":","}
        ]}
    });
    let (status, json) = post_json(&app, "/query", body).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(json["error_kind"], "INVALID_REQUEST");

    // unknown column
    let body = json!({
        "query_id": "http-missing",
        "schema": [{"name":"v","data_type":"int64"}],
        "columns": {"v": [1]},
        "plan": {"aggregations": [
            {"alias":"m","column":"nope","op":"mode"}
        ]}
    });
    let (status, json) = post_json(&app, "/query", body).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(json["error_kind"], "INVALID_REQUEST");
    assert!(json["decision"]["reason"]
        .as_str()
        .unwrap()
        .contains("nope"));
}

#[tokio::test]
async fn cancel_then_resume_over_http_finishes_the_query() {
    let (state, _dir) = state();
    let app = test_router(state);

    let plan = json!({
        "group_by": ["g"],
        "aggregations": [
            {"alias":"m","column":"tag","op":"mode"},
            {"alias":"med","column":"score","op":"percentile_cont","quantile":0.5}
        ]
    });

    // Deterministic diagnostic hook: the query cancels itself during the
    // merge's first cancellation point (server checks every 4 records),
    // exercising exactly the external-sort cancel/resume path without a race.
    let body = json!({
        "query_id": "http-cancel",
        "fixture": "skewed_groups",
        "cancel_after_merge_checks": 1,
        "plan": plan
    });

    let (status, json) = post_json(&app, "/query", body).await;
    assert_eq!(status, StatusCode::from_u16(499).unwrap(), "{json}");
    assert_eq!(json["error_kind"], "CANCELLED");
    assert_eq!(json["decision"]["verdict"], "undetermined");
    assert_eq!(json["decision"]["key_state"]["resumable"], true);
    assert_eq!(json["resume_token"]["query_id"], "http-cancel");
    assert_eq!(json["decision"]["key_state"]["spill_complete"], true);
    assert!(
        json["decision"]["key_state"]["spill_runs"]
            .as_u64()
            .unwrap()
            >= 1
    );

    // The explicit cancel endpoint reports the query as no longer running but
    // does not destroy durable state.
    let (_, cancel_json) =
        post_json(&app, "/query/cancel", json!({"query_id": "http-cancel"})).await;
    assert_eq!(cancel_json["cancelled"], false);

    // Resume: same query id + plan → concrete correct result.
    let resume_body = json!({"query_id": "http-cancel", "plan": plan});
    let (status, resumed) = post_json(&app, "/query/resume", resume_body).await;
    assert_eq!(status, StatusCode::OK, "{resumed}");
    let groups = resumed["data"]["groups"].as_array().unwrap();
    let heavy = groups
        .iter()
        .find(|g| g["group"]["g"] == "heavy")
        .expect("heavy group present");
    assert_eq!(heavy["aggregations"]["med"], 999.5); // median of 0..2000
}

#[tokio::test]
async fn resume_with_wrong_plan_is_conflict() {
    let (state, _dir) = state();
    let app = test_router(state.clone());
    // Establish spill state via a normal query.
    let body = json!({
        "query_id": "http-resume-bad",
        "fixture": "mode_tie",
        "plan": {
            "group_by": ["g"],
            "aggregations": [{"alias":"m","column":"tag","op":"mode"}]
        }
    });
    let (status, _) = post_json(&app, "/query", body).await;
    assert_eq!(status, StatusCode::OK);

    let wrong = json!({
        "query_id": "http-resume-bad",
        "plan": {
            "group_by": ["g"],
            "aggregations": [
                {"alias":"m","column":"score","op":"percentile_cont","quantile":0.5}
            ]
        }
    });
    let (status, json) = post_json(&app, "/query/resume", wrong).await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(json["error_kind"], "INVALID_RESUME");
}

#[tokio::test]
async fn unsafe_query_id_is_rejected() {
    let (state, _dir) = state();
    let app = test_router(state);
    let body = json!({
        "query_id": "../escape",
        "fixture": "mode_tie",
        "plan": {"aggregations": [{"alias":"m","column":"tag","op":"mode"}]}
    });
    let (status, json) = post_json(&app, "/query", body).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(json["error_kind"], "INVALID_REQUEST");
}
