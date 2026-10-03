//! HTTP API smoke test: drives the Axum router in-process (no socket) and
//! asserts concrete response payloads, not just status codes.

mod common;

use arc_cache::api;
use arc_cache::engine::Engine;
use arc_cache::store::MemStore;
use axum::body::Body;
use axum::http::{Request, StatusCode};
use tower::ServiceExt;

use common::{fixture_content, never_fail, ScriptedWriteback};

fn test_app() -> axum::Router {
    let store = MemStore::new();
    store.put(1, fixture_content(1));
    let wb = ScriptedWriteback {
        store: store.clone(),
        fail: never_fail(),
    };
    let engine = Engine::new(2, Box::new(store), Box::new(wb), 64, 4, false);
    api::router(engine)
}

async fn call(app: &axum::Router, req: Request<Body>) -> (StatusCode, serde_json::Value) {
    let resp = app.clone().oneshot(req).await.unwrap();
    let status = resp.status();
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20).await.unwrap();
    let json = serde_json::from_slice(&bytes).unwrap_or(serde_json::Value::Null);
    (status, json)
}

fn post_json(uri: &str, body: serde_json::Value) -> Request<Body> {
    Request::post(uri)
        .header("content-type", "application/json")
        .body(Body::from(body.to_string()))
        .unwrap()
}

#[tokio::test]
async fn api_end_to_end() {
    let app = test_app();

    let (status, _body) = call(&app, Request::get("/healthz").body(Body::empty()).unwrap()).await;
    assert_eq!(status, StatusCode::OK);

    // Read miss -> fill.
    let (status, body) = call(
        &app,
        post_json("/v1/access", serde_json::json!({"op": "read", "page": 1})),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(body["status"], "ok");
    assert_eq!(body["outcome"], "miss_fill");
    assert_eq!(body["data_hex"], serde_json::json!(hex_of(&fixture_content(1))));
    let request_id = body["request_id"].as_str().unwrap().to_string();

    // Same page again -> hit.
    let (_, body) = call(
        &app,
        post_json("/v1/access", serde_json::json!({"op": "read", "page": 1})),
    )
    .await;
    assert_eq!(body["outcome"], "hit_t1");

    // Write without payload -> 400 with a category.
    let (status, body) = call(
        &app,
        post_json("/v1/access", serde_json::json!({"op": "write", "page": 2})),
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"]["category"], "bad_request");

    // Read of a page absent from the store -> 409 not_found.
    let (status, body) = call(
        &app,
        post_json("/v1/access", serde_json::json!({"op": "read", "page": 99})),
    )
    .await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["error"]["category"], "not_found");

    // Stats reflect the traffic so far.
    let (_, body) = call(&app, Request::get("/v1/stats").body(Body::empty()).unwrap()).await;
    assert_eq!(body["stats"]["hits_t1"], 1);
    assert_eq!(body["stats"]["misses"], 1);
    assert_eq!(body["capacity"], 2);

    // Diagnostics carry the caller-supplied request id and redacted keys.
    let (_, body) = call(
        &app,
        post_json(
            "/v1/access",
            serde_json::json!({"request_id": "trace-me", "op": "read", "page": 1}),
        ),
    )
    .await;
    assert_eq!(body["request_id"], "trace-me");
    let (_, body) = call(
        &app,
        Request::get("/v1/diagnostics?limit=10")
            .body(Body::empty())
            .unwrap(),
    )
    .await;
    let records = body["records"].as_array().unwrap();
    let mine = records
        .iter()
        .find(|r| r["request_id"] == "trace-me")
        .expect("recorded");
    assert!(mine["key"].as_str().unwrap().starts_with("pg#"));
    assert_eq!(mine["decision"]["kind"], "accepted");
    let _ = request_id;

    // Resize to zero flips reads to read-through.
    let (status, _) = call(
        &app,
        post_json("/v1/resize", serde_json::json!({"capacity": 0})),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let (_, body) = call(
        &app,
        post_json("/v1/access", serde_json::json!({"op": "read", "page": 1})),
    )
    .await;
    assert_eq!(body["outcome"], "read_through");

    let (_, body) = call(&app, Request::get("/v1/state").body(Body::empty()).unwrap()).await;
    assert_eq!(body["capacity"], 0);
    assert_eq!(body["lists"]["t1"], serde_json::json!([]));
}

fn hex_of(data: &[u8]) -> String {
    arc_cache::hexutil::encode(data)
}
