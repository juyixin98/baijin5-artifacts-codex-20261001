//! End-to-end HTTP tests against the real Axum router:
//! success paths, every stable failure category, pagination resumption and
//! the Arrow2 IPC content type.

mod support;

use std::sync::Arc;

use axum::body::to_bytes;
use axum::http::{Request, StatusCode};
use leapfrog_triejoin::build_router;
use leapfrog_triejoin::config::Config;
use leapfrog_triejoin::state::AppState;
use support::{int_spec, triangle_relations};
use tower::ServiceExt;

/// Collect a response body into bytes, carrying status and headers.
async fn collect(
    response: axum::response::Response,
) -> (StatusCode, axum::http::HeaderMap, Vec<u8>) {
    let status = response.status();
    let headers = response.headers().clone();
    let bytes = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    (status, headers, bytes.to_vec())
}

fn app() -> axum::Router {
    build_router(Arc::new(AppState::new(Config::default())))
}

fn triangle_request_k4() -> serde_json::Value {
    // Reuse the in-memory builder shapes, then map to wire specs.
    let to_spec = |rel: &leapfrog_triejoin::schema::Relation| {
        serde_json::json!({
            "name": rel.name,
            "columns": rel.columns.iter().map(|c| serde_json::json!({
                "name": c.name, "type": c.typ.as_str()
            })).collect::<Vec<_>>(),
            "rows": rel.rows.iter().map(|row| row.iter().map(|cell| match cell {
                leapfrog_triejoin::value::Cell::Value(leapfrog_triejoin::value::Scalar::Int(i)) => serde_json::Value::from(*i),
                leapfrog_triejoin::value::Cell::Null => serde_json::Value::Null,
                _ => serde_json::Value::Null,
            }).collect::<Vec<_>>()).collect::<Vec<_>>(),
        })
    };
    let rels = triangle_relations(4)
        .iter()
        .map(to_spec)
        .collect::<Vec<_>>();
    serde_json::json!({ "relations": rels, "compare_naive": true })
}

async fn post(
    router: &axum::Router,
    uri: &str,
    content_type: &str,
    body: Vec<u8>,
) -> (StatusCode, axum::http::HeaderMap, Vec<u8>) {
    let request = Request::builder()
        .method("POST")
        .uri(uri)
        .header("content-type", content_type)
        .body(axum::body::Body::from(body))
        .unwrap();
    let response = router.clone().oneshot(request).await.unwrap();
    collect(response).await
}

async fn post_json(
    router: &axum::Router,
    uri: &str,
    value: serde_json::Value,
) -> (StatusCode, axum::http::HeaderMap, serde_json::Value) {
    let (status, headers, body) = post(
        router,
        uri,
        "application/json",
        serde_json::to_vec(&value).unwrap(),
    )
    .await;
    let parsed = serde_json::from_slice(&body).unwrap_or_else(|e| {
        panic!(
            "non-JSON response: {e}; body={}",
            String::from_utf8_lossy(&body)
        )
    });
    (status, headers, parsed)
}

#[tokio::test]
async fn health_and_empty_catalog_listing() {
    let router = app();
    let request = Request::builder()
        .uri("/health")
        .body(axum::body::Body::empty())
        .unwrap();
    let response = router.clone().oneshot(request).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);

    let request = Request::builder()
        .uri("/relations")
        .body(axum::body::Body::empty())
        .unwrap();
    let response = router.clone().oneshot(request).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let (_, _, body) = collect(response).await;
    let value: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(value["relations"], serde_json::json!([]));
}

#[tokio::test]
async fn triangle_query_accepted_with_naive_match() {
    let router = app();
    let (status, _, value) = post_json(&router, "/query", triangle_request_k4()).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(value["decision"], "accepted");
    assert_eq!(value["row_count"], 4, "K4 has four triangles");
    assert_eq!(value["stats"]["intermediate_tuples_materialized"], 0);
    assert_eq!(value["stats"]["stop_reason"], "complete");
    assert_eq!(value["naive"]["matches"], true);
    assert_eq!(value["naive"]["naive_emitted_rows"], 4);
    assert!(value["request_id"].as_str().unwrap().starts_with("req-"));
}

#[tokio::test]
async fn malformed_and_schema_invalid_json_are_distinguished() {
    let router = app();

    let (status, _, body) = post(
        &router,
        "/query",
        "application/json",
        b"{ not json".to_vec(),
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    let value: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(value["error"]["code"], "malformed_json");

    let (status, _, value) = post_json(
        &router,
        "/query",
        serde_json::json!({ "relations": [{"name": "r", "columns": []}] }),
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    // Zero-column relations are rejected by schema validation.
    assert_eq!(value["error"]["code"], "invalid_request");
}

#[tokio::test]
async fn failure_categories_are_explicit() {
    let router = app();

    // Unknown catalog relation -> 422.
    let (status, _, value) = post_json(
        &router,
        "/query",
        serde_json::json!({ "relation_names": ["ghost"] }),
    )
    .await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(value["error"]["code"], "unknown_relation");

    // Disjoint schema -> 400.
    let disjoint = serde_json::json!({
        "relations": [
            int_spec("r", &["a"], vec![vec![1]]),
            int_spec("s", &["b"], vec![vec![1]]),
        ]
    });
    let (status, _, value) = post_json(&router, "/query", disjoint).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(value["error"]["code"], "disjoint_schema");

    // Type mismatch on shared column 'a' -> 422.
    let mismatch = serde_json::json!({
        "relations": [
            { "name": "r", "columns": [{"name": "a", "type": "int"}], "rows": [[1]] },
            { "name": "s", "columns": [{"name": "a", "type": "string"}], "rows": [["x"]] },
        ]
    });
    let (status, _, value) = post_json(&router, "/query", mismatch).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(value["error"]["code"], "type_mismatch");

    // NULL join key under default reject policy -> 400.
    let null_key = serde_json::json!({
        "relations": [
            { "name": "r", "columns": [{"name": "a", "type": "int"}], "rows": [[null]] },
            { "name": "s", "columns": [{"name": "a", "type": "int"}], "rows": [[1]] },
        ]
    });
    let (status, _, value) = post_json(&router, "/query", null_key).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(value["error"]["code"], "null_key");

    // Oversized join (>6 relations) -> 422 unsupported_shape.
    let mut relations = Vec::new();
    for i in 0..7 {
        relations.push(int_spec(&format!("r{i}"), &["a", "b"], vec![vec![1, 1]]));
    }
    let oversized = serde_json::json!({ "relations": relations });
    let (status, _, value) = post_json(&router, "/query", oversized).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(value["error"]["code"], "unsupported_shape");
}

#[tokio::test]
async fn invalid_cursor_is_rejected_and_consumed() {
    let router = app();
    let request = serde_json::json!({
        "relations": [
            int_spec("r", &["a", "b"], vec![vec![1, 9]]),
            int_spec("s", &["b", "c"], vec![vec![9, 5]]),
        ],
        "cursor": "deadbeef-does-not-exist"
    });
    let (status, _, value) = post_json(&router, "/query", request).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(value["error"]["code"], "invalid_cursor");
}

#[tokio::test]
async fn pagination_over_http_is_lossless() {
    let router = app();
    // R(a,b): a=1..6 all with b=1 ; S(b,c): (1,10),(1,11) -> 12 rows total.
    let r_rows: Vec<Vec<i64>> = (1..=6).map(|a| vec![a, 1]).collect();
    let base = serde_json::json!({
        "relations": [
            int_spec("r", &["a", "b"], r_rows),
            int_spec("s", &["b", "c"], vec![vec![1, 10], vec![1, 11]]),
        ]
    });

    let mut collected: Vec<serde_json::Value> = Vec::new();
    let mut cursor: Option<String> = None;
    for page in 0..8 {
        let mut request = base.clone();
        request["limit"] = serde_json::Value::from(5);
        if let Some(token) = &cursor {
            request["cursor"] = serde_json::Value::from(token.as_str());
        }
        let (status, _, value) = post_json(&router, "/query", request).await;
        assert_eq!(status, StatusCode::OK, "page {page}");
        collected.extend(value["rows"].as_array().unwrap().clone());
        match value["next_cursor"].as_str() {
            Some(token) => cursor = Some(token.to_string()),
            None => {
                assert_eq!(value["stats"]["stop_reason"], "complete");
                break;
            }
        }
        assert!(page < 7, "pagination never finished");
    }
    assert_eq!(collected.len(), 12);
    let mut rows: Vec<(i64, i64, i64)> = collected
        .iter()
        .map(|r| {
            (
                r[0].as_i64().unwrap(),
                r[1].as_i64().unwrap(),
                r[2].as_i64().unwrap(),
            )
        })
        .collect();
    rows.sort();
    let mut expected: Vec<(i64, i64, i64)> =
        (1..=6).flat_map(|a| [(a, 1, 10), (a, 1, 11)]).collect();
    expected.sort();
    assert_eq!(rows, expected, "no rows lost or duplicated across cursors");
}

#[tokio::test]
async fn access_budget_returns_undecidable_then_resumes_to_completion() {
    let router = app();
    let r_rows: Vec<Vec<i64>> = (0..20).map(|a| vec![a, a]).collect();
    let s_rows: Vec<Vec<i64>> = (0..20).map(|b| vec![b, b * 100]).collect();
    let base = serde_json::json!({
        "relations": [
            int_spec("r", &["a", "b"], r_rows),
            int_spec("s", &["b", "c"], s_rows),
        ],
        "access_budget": 4
    });

    let (status, headers, partial) = post_json(&router, "/query", base.clone()).await;
    assert_eq!(status, StatusCode::ACCEPTED, "budget exhaustion -> 202");
    assert_eq!(partial["decision"], "undecidable");
    assert_eq!(partial["stats"]["stop_reason"], "budget_exhausted");
    assert!(partial["next_cursor"].is_string());
    assert!(headers.get("x-request-id").is_some());

    let mut request = base;
    request["cursor"] = partial["next_cursor"].clone();
    request.as_object_mut().unwrap().remove("access_budget");
    let (status, _, rest) = post_json(&router, "/query", request).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(rest["decision"], "accepted");

    let partial_n = partial["row_count"].as_u64().unwrap();
    let rest_n = rest["row_count"].as_u64().unwrap();
    assert_eq!(
        partial_n + rest_n,
        20,
        "exact complement of the full answer"
    );
}

#[tokio::test]
async fn arrow_endpoint_returns_decodable_ipc_stream() {
    let router = app();
    let bytes = serde_json::to_vec(&triangle_request_k4()).unwrap();
    let (status, headers, body) = post(&router, "/query/arrow", "application/json", bytes).await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        headers["content-type"],
        "application/vnd.apache.arrow.stream"
    );
    assert_eq!(headers["x-decision"], "accepted");
    assert_eq!(headers["x-row-count"], "4");
    assert_eq!(headers["x-intermediate-tuples"], "0");

    use arrow2::io::ipc;
    let mut reader = std::io::Cursor::new(body);
    let metadata = ipc::read::read_stream_metadata(&mut reader).unwrap();
    assert_eq!(metadata.schema.fields.len(), 3);
    let stream = ipc::read::StreamReader::new(&mut reader, metadata, None);
    let mut total = 0usize;
    for state in stream {
        if let ipc::read::StreamState::Some(chunk) = state.unwrap() {
            total += chunk.len();
        }
    }
    assert_eq!(total, 4);
}
