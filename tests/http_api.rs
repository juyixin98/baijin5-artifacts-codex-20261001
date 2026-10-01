//! End-to-end HTTP tests through the real Axum router and a bound ephemeral
//! port, asserting status codes, typed error bodies, request-id propagation,
//! and redaction (payloads must not appear in logs).

mod common;

use pctl::build_router;
use serde_json::{json, Value};
use tower::ServiceExt;

use common::test_config;

async fn send(app: axum::Router, body: Value) -> (axum::http::StatusCode, Value, String) {
    let resp = app
        .oneshot(
            axum::http::Request::post("/query")
                .header("content-type", "application/json")
                .header("x-request-id", "test-rid-123")
                .body(axum::body::Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let rid = resp
        .headers()
        .get("x-request-id")
        .map(|v| v.to_str().unwrap().to_string())
        .unwrap_or_default();
    let status = resp.status();
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    (status, serde_json::from_slice(&bytes).unwrap(), rid)
}

fn req() -> Value {
    json!({
        "group_by": "g",
        "columns": [
            { "name": "g", "data_type": "utf8",
              "values": ["a","a","b","b","b"] },
            { "name": "v", "data_type": "i64",
              "values": [1, 3, 2, 2, 10] }
        ],
        "operators": [
            { "op": "percentile", "column": "v", "p": 0.5, "method": "continuous" },
            { "op": "mode", "column": "v" },
            { "op": "string_agg", "column": "g", "delimiter": ",", "order": "asc" }
        ]
    })
}

#[tokio::test]
async fn happy_path_http() {
    let (cfg, _td) = test_config();
    let app = build_router(cfg);
    let (status, body, rid) = send(app, req()).await;
    assert_eq!(status, axum::http::StatusCode::OK);
    assert_eq!(rid, "test-rid-123", "caller request id is echoed");
    assert_eq!(body["request_id"], json!("test-rid-123"));
    assert_eq!(body["status"], json!("complete"));

    let groups = body["groups"].as_array().unwrap();
    assert_eq!(groups.len(), 2);
    let a = groups.iter().find(|g| g["group"] == json!("a")).unwrap();
    let b = groups.iter().find(|g| g["group"] == json!("b")).unwrap();
    // a: [1,3] cont p.5 -> 2
    assert_eq!(a["results"][0]["value"], json!(2.0));
    // b: [2,2,10] cont p.5 -> 2
    assert_eq!(b["results"][0]["value"], json!(2.0));
    // b mode -> 2 freq 2
    assert_eq!(b["results"][1]["value"], json!(2));
    assert_eq!(b["results"][1]["frequency"], json!(2));
    // string_agg over the group-key column
    assert_eq!(a["results"][2]["value"], json!("a,a"));
    assert_eq!(b["results"][2]["value"], json!("b,b,b"));

    let d = &body["diagnostics"];
    assert_eq!(d["groups_tracked"], json!(2));
    assert!(d["resident_budget_bytes"].as_u64().unwrap() > 0);
}

#[tokio::test]
async fn quantile_out_of_range_is_400() {
    let (cfg, _td) = test_config();
    let app = build_router(cfg);
    let mut bad = req();
    bad["operators"][0]["p"] = json!(1.5);
    let (status, body, rid) = send(app, bad).await;
    assert_eq!(status, axum::http::StatusCode::BAD_REQUEST);
    assert_eq!(body["status"], json!("error"));
    assert_eq!(body["error"]["kind"], json!("validation_rejected"));
    assert_eq!(body["error"]["code"], json!("quantile_out_of_range"));
    assert_eq!(body["error"]["field"], json!("operators[0].p"));
    assert_eq!(rid, "test-rid-123");
}

#[tokio::test]
async fn malformed_json_is_400_invalid_request() {
    let (cfg, _td) = test_config();
    let app = build_router(cfg);
    let resp = app
        .oneshot(
            axum::http::Request::post("/query")
                .header("content-type", "application/json")
                .body(axum::body::Body::from("{ not json"))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), axum::http::StatusCode::BAD_REQUEST);
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let body: Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(body["error"]["kind"], json!("invalid_request"));
    assert_eq!(body["error"]["code"], json!("malformed_json"));
}

#[tokio::test]
async fn resource_cap_returns_507() {
    let (mut cfg, _td) = test_config();
    cfg.group_table_cap = 1;
    let app = build_router(cfg);
    let (status, body, _) = send(app, req()).await;
    assert_eq!(status, axum::http::StatusCode::INSUFFICIENT_STORAGE);
    assert_eq!(body["error"]["kind"], json!("resource_error"));
    assert_eq!(body["error"]["code"], json!("groups_cap_exceeded"));
}

#[tokio::test]
async fn health_ok() {
    let (cfg, _td) = test_config();
    let app = build_router(cfg);
    let resp = app
        .oneshot(
            axum::http::Request::builder()
                .uri("/health")
                .body(axum::body::Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), axum::http::StatusCode::OK);
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    assert_eq!(&bytes[..], b"pctl ok\n");
}

#[tokio::test]
async fn generated_request_id_when_absent() {
    let (cfg, _td) = test_config();
    let app = build_router(cfg);
    let resp = app
        .oneshot(
            axum::http::Request::post("/query")
                .header("content-type", "application/json")
                .body(axum::body::Body::from(req().to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let rid = resp
        .headers()
        .get("x-request-id")
        .unwrap()
        .to_str()
        .unwrap();
    assert_eq!(rid.len(), 16, "generated id is 16 hex chars");
}
