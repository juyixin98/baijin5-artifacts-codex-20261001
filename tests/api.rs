//! 诊断接口测试：请求标识回显、接受/拒绝路径、增量与不可确定区间查询。

mod common;

use std::sync::{Arc, Mutex};

use axum::body::{Body, Bytes};
use axum::http::{Request, StatusCode};
use proc_diff::diag::{self, AppState};
use proc_diff::engine::Engine;
use proc_diff::store::Store;
use serde_json::{json, Value};
use tower::ServiceExt;

fn test_app() -> axum::Router {
    let dir = tempfile::tempdir().expect("tempdir");
    let store = Store::open(dir.path()).expect("store");
    // Leak the tempdir handle so it lives as long as the test's store dir.
    std::mem::forget(dir);
    let engine = Engine::new(proc_diff::engine::EngineConfig {
        counter_max: u64::MAX,
        wrap_max_plausible_delta: 360000,
    });
    diag::router(Arc::new(AppState { engine: Mutex::new(engine), store }))
}

async fn body_json(resp: axum::response::Response) -> Value {
    let bytes: Bytes = axum::body::to_bytes(resp.into_body(), 1 << 20).await.unwrap();
    serde_json::from_slice(&bytes).expect("json body")
}

fn post_json(uri: &str, req_id: &str, body: Value) -> Request<Body> {
    Request::builder()
        .method("POST")
        .uri(uri)
        .header("content-type", "application/json")
        .header("x-request-id", req_id)
        .body(Body::from(body.to_string()))
        .unwrap()
}

#[tokio::test]
async fn ingest_query_and_reject_flow() {
    let app = test_app();

    // health
    let resp = app
        .clone()
        .oneshot(Request::builder().uri("/healthz").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);

    // 1) 接受第一份快照（按目录采集），请求标识回显。
    let resp = app
        .clone()
        .oneshot(post_json(
            "/v1/snapshots",
            "req-accept-1",
            json!({"snapshot_dir": "fixtures/pid-reuse/snapshots/0001"}),
        ))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::ACCEPTED);
    assert_eq!(resp.headers().get("x-request-id").unwrap(), "req-accept-1");
    let body = body_json(resp).await;
    assert_eq!(body["outcome"], "accepted");
    assert_eq!(body["seq"], 1);
    assert_eq!(body["procs"], 2);

    // 2) 重复 seq 必须拒绝且给出原因。
    let resp = app
        .clone()
        .oneshot(post_json(
            "/v1/snapshots",
            "req-dup",
            json!({"snapshot_dir": "fixtures/pid-reuse/snapshots/0001"}),
        ))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::CONFLICT);
    let body = body_json(resp).await;
    assert_eq!(body["outcome"], "rejected");
    assert!(body["reason"].as_str().unwrap().contains("DuplicateOrOutOfOrder"));
    assert_eq!(body["request_id"], "req-dup");

    // 3) 第二份快照：PID 复用。
    let resp = app
        .clone()
        .oneshot(post_json(
            "/v1/snapshots",
            "req-accept-2",
            json!({"snapshot_dir": "fixtures/pid-reuse/snapshots/0002"}),
        ))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::ACCEPTED);

    // 4) 增量查询：pid 100 两个身份，第二个为 identity_reset。
    let resp = app
        .clone()
        .oneshot(Request::builder().uri("/v1/deltas?pid=100").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = body_json(resp).await;
    let deltas = body["deltas"].as_array().unwrap();
    assert_eq!(deltas.len(), 2);
    assert_eq!(deltas[1]["identity"]["start_generation"], 9);
    assert_eq!(deltas[1]["category"], "identity_reset");
    assert!(deltas[1]["delta"].is_null());

    // 5) 该场景无不可确定区间。
    let resp = app
        .oneshot(
            Request::builder()
                .uri("/v1/intervals/undetermined")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let body = body_json(resp).await;
    assert_eq!(body["intervals"].as_array().unwrap().len(), 0);
}

#[tokio::test]
async fn inline_snapshot_counter_anomaly_is_reported() {
    let app = test_app();
    let snap = |seq: u64, utime: u64| {
        json!({
            "snapshot": {
                "meta": {"seq": seq, "taken_at_ms": seq * 1000},
                "procs": [{
                    "identity": {"pid": 42, "start_generation": 1},
                    "ppid": 1, "utime": utime, "stime": 0,
                    "rss_bytes": 128, "state": "R"
                }],
                "read_failures": []
            }
        })
    };

    let resp = app
        .clone()
        .oneshot(post_json("/v1/snapshots", "req-inline-1", snap(1, 900)))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::ACCEPTED);

    // u64 计数器从 900 回退到 10，远超回绕阈值 → 数据异常。
    let resp = app
        .clone()
        .oneshot(post_json("/v1/snapshots", "req-inline-2", snap(2, 10)))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::ACCEPTED);

    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/v1/intervals/undetermined")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let body = body_json(resp).await;
    let intervals = body["intervals"].as_array().unwrap();
    assert_eq!(intervals.len(), 1);
    assert_eq!(intervals[0]["category"], "counter_anomaly");
    assert_eq!(intervals[0]["identity"]["pid"], 42);
    assert_eq!(intervals[0]["delta_known"], false);

    // 未提供 x-request-id 时服务端生成一个。
    let resp = app
        .oneshot(Request::builder().uri("/v1/tree").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert!(resp.headers().get("x-request-id").is_some());
    let body = body_json(resp).await;
    assert!(!body["request_id"].as_str().unwrap().is_empty());
    assert_eq!(body["nodes"].as_array().unwrap().len(), 1);
}

#[tokio::test]
async fn ambiguous_ingest_request_is_rejected() {
    let app = test_app();
    let resp = app
        .oneshot(post_json("/v1/snapshots", "req-bad", json!({})))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_eq!(body["outcome"], "rejected");
}
