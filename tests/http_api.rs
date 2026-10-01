//! End-to-end HTTP tests against the real Axum router (no network
//! listener) using tower's in-memory `oneshot`. Asserts status codes,
//! the categorized error envelope, concrete result bodies and cursor
//! pagination over the wire.

use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use iejoin::api::{router, AppState};
use iejoin::state::SessionStore;
use iejoin::trace::Tracer;
use tower::util::ServiceExt;

fn app() -> axum::Router {
    router(AppState::new(Tracer::new(None), SessionStore::new()))
}

async fn post(
    app: &axum::Router,
    uri: &str,
    json: serde_json::Value,
) -> (StatusCode, serde_json::Value) {
    let req = Request::builder()
        .method("POST")
        .uri(uri)
        .header("content-type", "application/json")
        .body(Body::from(serde_json::to_vec(&json).unwrap()))
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    let status = resp.status();
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    let v: serde_json::Value = serde_json::from_slice(&bytes).unwrap_or(serde_json::Value::Null);
    (status, v)
}

fn join_body() -> serde_json::Value {
    serde_json::json!({
        "left": {
            "columns": [
                {"name": "x", "values": [1, 2, 3]},
                {"name": "y", "values": [5, 4, 3]}
            ],
            "row_ids": ["l0", "l1", "l2"]
        },
        "right": {
            "columns": [
                {"name": "x", "values": [3]},
                {"name": "y", "values": [4]}
            ],
            "row_ids": ["ra"]
        },
        "predicates": [
            {"left_column": "x", "op": "<=", "right_column": "x"},
            {"left_column": "y", "op": "<=", "right_column": "y"}
        ]
    })
}

#[tokio::test]
async fn healthz_ok() {
    let req = Request::builder()
        .uri("/healthz")
        .body(Body::empty())
        .unwrap();
    let resp = app().oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
}

#[tokio::test]
async fn join_returns_concrete_pairs_and_counters() {
    let app = app();
    let (status, body) = post(&app, "/v1/join", join_body()).await;
    assert_eq!(status, StatusCode::OK, "body: {body}");
    assert_eq!(body["count"], 2);
    assert_eq!(body["truncated"], false);
    let pairs = body["pairs"].as_array().unwrap();
    let mut ids: Vec<String> = pairs
        .iter()
        .map(|p| {
            format!(
                "{}:{}",
                p["left_id"].as_str().unwrap(),
                p["right_id"].as_str().unwrap()
            )
        })
        .collect();
    ids.sort();
    assert_eq!(ids, vec!["l1:ra", "l2:ra"]);
    // Concrete instrumentation is present.
    assert!(body["counters"]["candidate_accesses"].as_u64().unwrap() >= 2);
    assert!(body["run_id"].as_str().unwrap().starts_with("run-"));
}

#[tokio::test]
async fn malformed_json_is_input_400_envelope() {
    let req = Request::builder()
        .method("POST")
        .uri("/v1/join")
        .header("content-type", "application/json")
        .body(Body::from("{ not json"))
        .unwrap();
    let resp = app().oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    let body: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(body["category"], "input");
    assert_eq!(body["code"], "invalid_json");
}

#[tokio::test]
async fn bad_plan_is_400_with_category_and_code() {
    let app = app();
    let mut body = join_body();
    body["predicates"] = serde_json::json!([
        {"left_column": "x", "op": "=", "right_column": "x"},
        {"left_column": "y", "op": "<", "right_column": "y"}
    ]);
    let (status, parsed) = post(&app, "/v1/join", body).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(parsed["category"], "input");
    assert_eq!(parsed["code"], "unsupported_comparator");
}

#[tokio::test]
async fn oversized_input_is_413() {
    let app = app();
    let mut body = join_body();
    body["budget"] = serde_json::json!({"max_input_rows": 2});
    // left has 3 rows -> exceeds.
    let (status, parsed) = post(&app, "/v1/join", body).await;
    assert_eq!(status, StatusCode::PAYLOAD_TOO_LARGE);
    assert_eq!(parsed["category"], "resource_exhausted");
    assert_eq!(parsed["code"], "input_too_large");
}

#[tokio::test]
async fn cursor_lifecycle_over_http() {
    let app = app();
    // Open with page_size 1 over the 2-pair <= scenario.
    let mut open = join_body();
    open["page_size"] = serde_json::json!(1);
    let (status, page1) = post(&app, "/v1/cursors", open).await;
    assert_eq!(status, StatusCode::OK, "{page1}");
    assert_eq!(page1["count"], 1);
    assert_eq!(page1["finished"], false);
    let cursor_id = page1["cursor_id"].as_str().unwrap().to_owned();

    let (status, page2) = post(
        &app,
        "/v1/cursors/next",
        serde_json::json!({"cursor_id": cursor_id, "page_size": 1}),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(page2["count"], 1);
    assert_eq!(page2["finished"], true);

    // Cursor removed after finish -> 409 state conflict.
    let (status, err) = post(
        &app,
        "/v1/cursors/next",
        serde_json::json!({"cursor_id": cursor_id}),
    )
    .await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(err["category"], "state_conflict");
    assert_eq!(err["code"], "unknown_cursor");
}

#[tokio::test]
async fn unknown_cursor_is_409() {
    let app = app();
    let (status, err) = post(
        &app,
        "/v1/cursors/next",
        serde_json::json!({"cursor_id": "cur-nope"}),
    )
    .await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(err["code"], "unknown_cursor");
}
