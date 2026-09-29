//! End-to-end Axum tests through real HTTP requests (in-process, no network).
//! Asserts concrete results and the exact failure code/status per category.

mod common;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use iejoin_range::api::{router, AppState};
use iejoin_range::replay::ReplayLogger;
use serde_json::{json, Value};
use tower::ServiceExt;

fn app() -> axum::Router {
    AppState::new(8, ReplayLogger::memory(64)).pipe(router)
}

trait Pipe: Sized {
    fn pipe<F, U>(self, f: F) -> U
    where
        F: FnOnce(Self) -> U,
    {
        f(self)
    }
}
impl<T> Pipe for T {}

async fn post(router: axum::Router, uri: &str, body: Value) -> (StatusCode, Value) {
    let resp = router
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(uri)
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = resp.status();
    let bytes = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    let json = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
    (status, json)
}

async fn get(router: axum::Router, uri: &str) -> (StatusCode, Value) {
    let resp = router
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(uri)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let status = resp.status();
    let bytes = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    let json = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
    (status, json)
}

fn two_col_batch(prefix: &str, k1: Vec<i64>, k2: Vec<i64>) -> Value {
    json!({
        "columns": [
            {"name": format!("{prefix}1"), "type": "int64", "values": k1},
            {"name": format!("{prefix}2"), "type": "int64", "values": k2}
        ]
    })
}

/// a1 > b1 AND a2 < b2
fn gt_lt_plan() -> Value {
    json!({
        "p1": {"left_col": 0, "right_col": 0, "op": "gt"},
        "p2": {"left_col": 1, "right_col": 1, "op": "lt"}
    })
}

#[tokio::test]
async fn health_endpoint_reports_ok() {
    let (status, body) = get(app(), "/healthz").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["status"], json!("ok"));
}

#[tokio::test]
async fn join_returns_exact_pairs_and_candidate_count() {
    let body = json!({
        "plan": gt_lt_plan(),
        "left": two_col_batch("a", vec![5, 2, 5, 9], vec![7, 7, 1, 3]),
        "right": two_col_batch("b", vec![5, 1, 2, 9], vec![7, 9, 0, 4]),
    });
    let (status, resp) = post(app(), "/join", body).await;
    assert_eq!(status, StatusCode::OK, "{resp}");
    assert_eq!(resp["finished"], json!(true));
    assert_eq!(resp["truncation"], json!("complete"));
    // Hand answer: (0,1),(1,1),(2,1),(3,0),(3,1)
    let pairs = resp["pairs"].as_array().unwrap();
    assert_eq!(pairs.len(), 5);
    let mut tuples: Vec<(i64, i64)> = pairs
        .iter()
        .map(|p| {
            (
                p["left_row"].as_i64().unwrap(),
                p["right_row"].as_i64().unwrap(),
            )
        })
        .collect();
    tuples.sort_unstable();
    assert_eq!(tuples, vec![(0, 1), (1, 1), (2, 1), (3, 0), (3, 1)]);
    assert!(resp["stats"]["candidate_accesses"].as_u64().unwrap() < 4 * 4);
    assert!(resp["run_id"].as_str().unwrap().starts_with("run-"));
}

#[tokio::test]
async fn malformed_json_is_422_extractor_rejection() {
    let (status, _resp) = post(app(), "/join", json!({"plan": {}})).await;
    // Axum's Json extractor rejects shape-invalid bodies with 422 before the
    // handler runs; semantic validation inside the handler is what yields 400.
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert!(status.is_client_error());
}

#[tokio::test]
async fn incompatible_types_are_400_with_specific_code() {
    let body = json!({
        "plan": gt_lt_plan(),
        "left": two_col_batch("a", vec![1], vec![2]),
        "right": {
            "columns": [
                {"name": "b1", "type": "utf8", "values": ["x"]},
                {"name": "b2", "type": "int64", "values": [3]}
            ]
        }
    });
    let (status, resp) = post(app(), "/join", body).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(resp["error"]["code"], json!("incompatible_types"));
    assert_eq!(resp["error"]["category"], json!("input"));
}

#[tokio::test]
async fn unknown_comparator_is_rejected_at_decode() {
    let body = json!({
        "plan": {
            "p1": {"left_col": 0, "right_col": 0, "op": "!="},
            "p2": {"left_col": 1, "right_col": 1, "op": "lt"}
        },
        "left": two_col_batch("a", vec![1], vec![2]),
        "right": two_col_batch("b", vec![3], vec![4]),
    });
    let (status, resp) = post(app(), "/join", body).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    // Axum JSON extractor rejection before our handler; still a 4xx, not 200/500.
    assert!(status.is_client_error());
    let _ = resp;
}

#[tokio::test]
async fn output_over_budget_is_413_resource_error() {
    let body = json!({
        "plan": {
            "p1": {"left_col": 0, "right_col": 0, "op": "lt"},
            "p2": {"left_col": 1, "right_col": 1, "op": "lt"}
        },
        "left": two_col_batch("a", vec![1, 2], vec![1, 2]),
        "right": two_col_batch("b", vec![3, 4], vec![3, 4]),
        "budget": {"max_output": 2}
    });
    let (status, resp) = post(app(), "/join", body).await;
    assert_eq!(status, StatusCode::PAYLOAD_TOO_LARGE);
    assert_eq!(resp["error"]["code"], json!("budget_exceeded"));
    assert_eq!(resp["error"]["category"], json!("resource"));
}

#[tokio::test]
async fn paged_session_reassembles_full_result_and_run_is_queryable() {
    let router = app();
    let create = json!({
        "plan": {
            "p1": {"left_col": 0, "right_col": 0, "op": "lt"},
            "p2": {"left_col": 1, "right_col": 1, "op": "lt"}
        },
        "left": two_col_batch("a", vec![1, 2], vec![1, 2]),
        "right": two_col_batch("b", vec![3, 4], vec![3, 4]),
        "budget": {"max_output": 1}
    });
    let (status, first) = post(router.clone(), "/sessions", create).await;
    assert_eq!(status, StatusCode::OK, "{first}");
    assert_eq!(first["finished"], json!(false));
    assert_eq!(first["pairs"].as_array().unwrap().len(), 1);
    let session_id = first["session_id"].as_str().unwrap().to_string();
    let first_run = first["run_id"].as_str().unwrap().to_string();

    // The first run is already queryable.
    let (s, rec) = get(router.clone(), &format!("/runs/{first_run}")).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(rec["outcome"], json!("truncated"));

    let mut collected = first["pairs"].as_array().unwrap().clone();
    let mut cursor = first["next_cursor"].clone();
    let mut pages = 1;
    while !cursor.is_null() {
        let body = json!({"cursor": cursor});
        let (status, page) = post(
            router.clone(),
            &format!("/sessions/{session_id}/continue"),
            body,
        )
        .await;
        assert_eq!(status, StatusCode::OK, "{page}");
        collected.extend(page["pairs"].as_array().unwrap().iter().cloned());
        pages += 1;
        if page["finished"] == json!(true) {
            assert!(page["next_cursor"].is_null());
            break;
        }
        cursor = page["next_cursor"].clone();
        assert!(pages < 10, "paging must terminate");
    }
    assert_eq!(
        collected.len(),
        4,
        "2x2 full product reassembled across pages"
    );
}

#[tokio::test]
async fn stale_cursor_and_finished_and_unknown_session_are_409_state_errors() {
    let router = app();
    let create = json!({
        "plan": {
            "p1": {"left_col": 0, "right_col": 0, "op": "lt"},
            "p2": {"left_col": 1, "right_col": 1, "op": "lt"}
        },
        "left": two_col_batch("a", vec![1], vec![1]),
        "right": two_col_batch("b", vec![2], vec![2])
    });
    let (_, first) = post(router.clone(), "/sessions", create).await;
    assert_eq!(first["finished"], json!(true));
    let session_id = first["session_id"].as_str().unwrap().to_string();

    // Continuing a finished session → session_finished.
    let finished_cursor = json!({
        "session_id": session_id,
        "page_index": 1,
        "checkpoint": {"dpos": 1, "act_pos": 1, "probe_pos": 0, "probe_hi": 0},
        "truncation": "complete"
    });
    let (status, resp) = post(
        router.clone(),
        &format!("/sessions/{session_id}/continue"),
        json!({"cursor": finished_cursor}),
    )
    .await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(resp["error"]["code"], json!("session_finished"));

    // Unknown session → unknown_session.
    let (status, resp) = post(
        router.clone(),
        "/sessions/nope/continue",
        json!({"cursor": {
            "session_id": "nope", "page_index": 1,
            "checkpoint": {"dpos": 0, "act_pos": 0, "probe_pos": 0, "probe_hi": 0},
            "truncation": "output_limit"
        }}),
    )
    .await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(resp["error"]["code"], json!("unknown_session"));

    // Wrong session id embedded in cursor → cursor_mismatch.
    let (status, resp) = post(
        router.clone(),
        &format!("/sessions/{session_id}/continue"),
        json!({"cursor": {
            "session_id": "other", "page_index": 1,
            "checkpoint": {"dpos": 0, "act_pos": 0, "probe_pos": 0, "probe_hi": 0},
            "truncation": "output_limit"
        }}),
    )
    .await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(resp["error"]["code"], json!("cursor_mismatch"));
}

#[tokio::test]
async fn unknown_run_is_404() {
    let (status, resp) = get(app(), "/runs/run-999999").await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(resp["error"]["code"], json!("unknown_run"));
    assert_eq!(resp["error"]["category"], json!("state"));
}
