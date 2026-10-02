//! HTTP 诊断接口集成测试：状态码、错误类别、报告往返、事件日志。

use axum::body::{Body, to_bytes};
use axum::http::{Request, StatusCode};
use iosched_compare::api::{AppState, router};
use iosched_compare::config::ServerConfig;
use iosched_compare::state::RunStore;
use std::sync::Arc;
use tower::ServiceExt;

fn test_app() -> axum::Router {
    // 每个测试用独立数据目录，避免并行测试互相清库。
    static SEQ: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    let seq = SEQ.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let dir = std::env::temp_dir().join(format!("iosched-test-{}-{seq}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    let config = ServerConfig {
        listen_addr: "127.0.0.1:0".into(),
        data_dir: dir.to_string_lossy().into(),
        fixture_dir: "fixtures".into(),
        deadline: Default::default(),
    };
    let store = RunStore::new(&config.data_dir).unwrap();
    router(Arc::new(AppState { config, store }))
}

fn json_post(uri: &str, body: serde_json::Value) -> Request<Body> {
    Request::post(uri)
        .header("content-type", "application/json")
        .body(Body::from(body.to_string()))
        .unwrap()
}

async fn body_json(resp: axum::response::Response) -> serde_json::Value {
    let bytes = to_bytes(resp.into_body(), 1 << 20).await.unwrap();
    serde_json::from_slice(&bytes).unwrap()
}

fn small_trace() -> serde_json::Value {
    serde_json::json!({
        "requests": [
            { "id": "api-r1", "lba": 0,  "sectors": 8, "direction": "read", "arrival_ms": 0 },
            { "id": "api-r2", "lba": 64, "sectors": 8, "direction": "read", "arrival_ms": 0 }
        ],
        "cancels": []
    })
}

#[tokio::test]
async fn health_reports_time_model_disclaimer() {
    let app = test_app();
    let resp = app
        .oneshot(Request::get("/v1/health").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let json = body_json(resp).await;
    assert_eq!(json["status"], "ok");
    assert!(
        json["time_model"]
            .as_str()
            .unwrap()
            .contains("NOT an SSD measurement")
    );
}

#[tokio::test]
async fn create_run_then_read_back_report_and_events() {
    let app = test_app();
    let resp = app
        .clone()
        .oneshot(json_post(
            "/v1/runs",
            serde_json::json!({ "trace": small_trace() }),
        ))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::CREATED);
    let report = body_json(resp).await;
    let run_id = report["run_id"].as_str().unwrap().to_string();
    assert!(run_id.starts_with("run-"));
    assert_eq!(
        report["results"].as_array().unwrap().len(),
        2,
        "both schedulers run by default"
    );
    assert_eq!(report["results"][0]["metrics"]["submitted"], 2);
    assert!(report["service_version"].as_str().is_some());

    // 报告可读回，且内容一致（持久化生效）。
    let resp = app
        .clone()
        .oneshot(
            Request::get(format!("/v1/runs/{run_id}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let stored = body_json(resp).await;
    assert_eq!(stored["run_id"], run_id);
    assert_eq!(stored["results"][0]["metrics"]["submitted"], 2);

    // 事件日志：NDJSON，含下发记录与请求身份。
    let resp = app
        .clone()
        .oneshot(
            Request::get(format!("/v1/runs/{run_id}/events"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let bytes = to_bytes(resp.into_body(), 1 << 20).await.unwrap();
    let text = String::from_utf8(bytes.to_vec()).unwrap();
    assert!(
        text.contains("\"dispatched\""),
        "events log must contain dispatch steps"
    );
    assert!(
        text.contains("api-r1"),
        "events log must carry request identity"
    );

    // 列表接口包含该运行的摘要。
    let resp = app
        .oneshot(Request::get("/v1/runs").body(Body::empty()).unwrap())
        .await
        .unwrap();
    let list = body_json(resp).await;
    assert!(
        list["runs"]
            .as_array()
            .unwrap()
            .iter()
            .any(|r| r["run_id"] == run_id)
    );
}

#[tokio::test]
async fn fixture_run_uses_on_disk_trace() {
    let app = test_app();
    let resp = app
        .oneshot(json_post(
            "/v1/runs",
            serde_json::json!({ "fixture_name": "sequential" }),
        ))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::CREATED);
    let report = body_json(resp).await;
    assert_eq!(report["results"][0]["metrics"]["submitted"], 4);
}

#[tokio::test]
async fn error_semantics_are_categorized() {
    let app = test_app();

    // 400 malformed_request：请求体不是合法 JSON 结构。
    let resp = app
        .clone()
        .oneshot(
            Request::post("/v1/runs")
                .header("content-type", "application/json")
                .body(Body::from("{not json"))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    assert_eq!(
        body_json(resp).await["error"]["category"],
        "malformed_request"
    );

    // 400 malformed_request：trace 与 fixture_name 都缺。
    let resp = app
        .clone()
        .oneshot(json_post("/v1/runs", serde_json::json!({})))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);

    // 422 sector_out_of_range：轨迹校验失败，details 逐条列出。
    let bad = serde_json::json!({
        "trace": {
            "requests": [
                { "id": "oob", "lba": 2000000, "sectors": 8, "direction": "read", "arrival_ms": 0 }
            ]
        }
    });
    let resp = app
        .clone()
        .oneshot(json_post("/v1/runs", bad))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::UNPROCESSABLE_ENTITY);
    let json = body_json(resp).await;
    assert_eq!(json["error"]["category"], "sector_out_of_range");
    assert_eq!(json["error"]["details"][0]["request_id"], "oob");

    // 404 run_not_found。
    let resp = app
        .clone()
        .oneshot(
            Request::get("/v1/runs/run-999999")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    assert_eq!(body_json(resp).await["error"]["category"], "run_not_found");

    // 404 fixture_not_found（含路径穿越尝试）。
    let resp = app
        .clone()
        .oneshot(json_post(
            "/v1/runs",
            serde_json::json!({ "fixture_name": "nonexistent" }),
        ))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    assert_eq!(
        body_json(resp).await["error"]["category"],
        "fixture_not_found"
    );

    let resp = app
        .oneshot(json_post(
            "/v1/runs",
            serde_json::json!({ "fixture_name": "../etc/passwd" }),
        ))
        .await
        .unwrap();
    assert_eq!(
        resp.status(),
        StatusCode::NOT_FOUND,
        "path traversal must be rejected"
    );
}
