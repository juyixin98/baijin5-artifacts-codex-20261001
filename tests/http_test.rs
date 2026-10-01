//! HTTP-level tests: requests traverse the real Axum router, JSON
//! (de)serialization, status-code mapping, and the correlation-id machinery.

mod common;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use common::fixture_json;
use decorr::ast::LoadFixturesRequest;
use decorr::{build_app_state, http};
use tower::ServiceExt;

use serde_json::{json, Value};

fn app() -> axum::Router {
    let service = build_app_state();
    let req: LoadFixturesRequest =
        serde_json::from_value(fixture_json()).expect("fixture request deserializes");
    service.load_fixtures(&req).expect("fixtures load");
    http::router(service)
}

async fn post(router: &axum::Router, uri: &str, body: Value) -> (StatusCode, Value) {
    let response = router
        .clone()
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
    let status = response.status();
    let bytes = axum::body::to_bytes(response.into_body(), 1 << 20)
        .await
        .unwrap();
    let value: Value = serde_json::from_slice(&bytes).unwrap_or_else(|e| {
        panic!(
            "response was not JSON ({e}): {}",
            String::from_utf8_lossy(&bytes)
        )
    });
    (status, value)
}

#[tokio::test]
async fn health_and_index_report_version() {
    let response = app()
        .oneshot(
            Request::builder()
                .uri("/health")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body: Value = serde_json::from_slice(
        &axum::body::to_bytes(response.into_body(), 1 << 16)
            .await
            .unwrap(),
    )
    .unwrap();
    assert_eq!(body["status"], json!("ok"));
    assert!(body["engine_version"]
        .as_str()
        .unwrap()
        .contains("group-probe-v1"));
}

#[tokio::test]
async fn query_endpoint_returns_equivalent_results_with_trace_and_proof() {
    let body = json!({
        "query_id": "q-exists",
        "outer": { "relation": "orders", "select": ["o_id"] },
        "subquery": {
            "op": "exists",
            "inner_relation": "payments",
            "correlation": [ { "outer": "cust", "inner": "cust" } ],
            "output_column": "has_payment"
        }
    });
    let (status, value) = post(&app(), "/v1/query", body).await;
    assert_eq!(status, StatusCode::OK, "{value}");
    assert!(value["request_id"].is_string());
    assert_eq!(value["query_id"], json!("q-exists"));

    let flags: Vec<Value> = value["rows"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| r["has_payment"].clone())
        .collect();
    assert_eq!(
        flags,
        vec![
            json!(true),
            json!(true),
            json!(false),
            json!(true),
            json!(true),
            json!(false)
        ]
    );

    assert_eq!(value["equivalence"]["equivalent"], json!(true));
    assert_eq!(value["primary_executor"], json!("decorrelated"));

    // Both executor locations/versions are reported for explainability.
    let mut modes: Vec<&str> = value["executors"]
        .as_array()
        .unwrap()
        .iter()
        .map(|e| e["mode"].as_str().unwrap())
        .collect();
    modes.sort_unstable();
    assert_eq!(modes, vec!["decorrelated", "row_by_row"]);

    // Rewrite proof is present and mentions the semi-join identity.
    let proof = &value["rewrite"];
    assert!(proof["identity"].as_str().unwrap().contains("SEMI JOIN"));
    let hazards = proof["hazards_checked"].as_array().unwrap();
    assert!(hazards
        .iter()
        .any(|h| h.as_str().unwrap().contains("NOT IN")));
    assert!(!proof["not_claimed"].as_array().unwrap().is_empty());

    // Trace shows key steps with processing locations.
    let steps: Vec<&str> = value["trace"]
        .as_array()
        .unwrap()
        .iter()
        .map(|t| t["step"].as_str().unwrap())
        .collect();
    assert!(steps.contains(&"validate"));
    assert!(steps.contains(&"rewrite"));
    assert!(steps.contains(&"execute"));
    assert!(steps.contains(&"cross_check"));
}

#[tokio::test]
async fn not_in_returns_422_with_unsupported_category() {
    let body = json!({
        "outer": { "relation": "orders", "select": ["o_id"] },
        "subquery": {
            "op": "not_in",
            "inner_relation": "payments",
            "correlation": [ { "outer": "cust", "inner": "cust" } ],
            "output_column": "x"
        }
    });
    let (status, value) = post(&app(), "/v1/query", body).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(value["failure"]["category"], json!("unsupported_form"));
    assert_eq!(value["failure"]["unsupported_form"], json!("NOT IN"));
    assert!(value["failure"]["reason"]
        .as_str()
        .unwrap()
        .contains("NULL"));
    assert!(value["request_id"].is_string());
}

#[tokio::test]
async fn scalar_multirow_returns_422_and_same_reference_failure() {
    let body = json!({
        "outer": { "relation": "orders" },
        "subquery": {
            "op": "scalar",
            "inner_relation": "payments",
            "correlation": [ { "outer": "cust", "inner": "cust" } ],
            "value_column": "p_id",
            "output_column": "p"
        }
    });
    let (status, value) = post(&app(), "/v1/query", body).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        value["failure"]["category"],
        json!("scalar_cardinality_violation")
    );
    assert_eq!(value["failure_cross_check"], json!(true));
}

#[tokio::test]
async fn unknown_field_is_rejected_at_parse_time() {
    let body = json!({
        "outer": { "relation": "orders" },
        "subquery": {
            "op": "exists",
            "inner_relation": "payments",
            "correlation": [],
            "output_column": "x",
            "mystery": 1
        }
    });
    let (status, value) = post(&app(), "/v1/query", body).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(value["failure"]["category"], json!("validation_error"));
    assert_eq!(value["failure"]["location"], json!("request_body"));
}

#[tokio::test]
async fn missing_relation_returns_schema_error() {
    let body = json!({
        "outer": { "relation": "nope" },
        "subquery": {
            "op": "exists",
            "inner_relation": "payments",
            "correlation": [ { "outer": "cust", "inner": "cust" } ],
            "output_column": "x"
        }
    });
    let (status, value) = post(&app(), "/v1/query", body).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(value["failure"]["category"], json!("schema_error"));
}

#[tokio::test]
async fn empty_correlation_is_a_validation_error() {
    let body = json!({
        "outer": { "relation": "orders" },
        "subquery": {
            "op": "exists",
            "inner_relation": "payments",
            "correlation": [],
            "output_column": "x"
        }
    });
    let (status, value) = post(&app(), "/v1/query", body).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(value["failure"]["category"], json!("validation_error"));
}
