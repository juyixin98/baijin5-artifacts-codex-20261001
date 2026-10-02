//! End-to-end test through the Axum diagnostic interface (in-process, no
//! network): drives create/map/write/sync/truncate/unmap over HTTP and
//! asserts concrete JSON bodies, HTTP statuses and error categories —
//! including an injected sync failure.

mod common;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use common::{TestRun, PAGE};
use http_body_util::BodyExt;
use mmap_model::config::ModelConfig;
use mmap_model::diag;
use mmap_model::model::Vm;
use mmap_model::store::{FaultyStore, MemStore};
use serde_json::{json, Value};
use std::sync::{Arc, Mutex};
use tower::ServiceExt;

struct Api {
    app: axum::Router,
    faults: FaultyStore<MemStore>,
}

impl Api {
    fn new() -> Self {
        let cfg = ModelConfig {
            page_size: PAGE,
            max_file_size: 1 << 20,
            max_mappings: 64,
        };
        let store = FaultyStore::new(MemStore::new());
        let faults = store.clone();
        let vm = Vm::new(cfg, store);
        Api {
            app: diag::router(Arc::new(Mutex::new(vm))),
            faults,
        }
    }

    async fn call(&self, req: Request<Body>) -> (StatusCode, Value) {
        let resp = self.app.clone().oneshot(req).await.unwrap();
        let status = resp.status();
        let bytes = resp.into_body().collect().await.unwrap().to_bytes();
        let body = if bytes.is_empty() {
            Value::Null
        } else {
            serde_json::from_slice(&bytes).unwrap_or(Value::Null)
        };
        (status, body)
    }

    async fn post(&self, uri: &str, body: Value) -> (StatusCode, Value) {
        self.call(
            Request::post(uri)
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
    }

    async fn get(&self, uri: &str) -> (StatusCode, Value) {
        self.call(Request::get(uri).body(Body::empty()).unwrap())
            .await
    }

    async fn delete(&self, uri: &str) -> (StatusCode, Value) {
        self.call(Request::delete(uri).body(Body::empty()).unwrap())
            .await
    }
}

#[tokio::test]
async fn end_to_end_over_http() {
    let mut t = TestRun::new("api_end_to_end");
    let api = Api::new();

    t.step("http", "GET /version reports name+version+run_id");
    let (st, body) = api.get("/version").await;
    t.check("version status", &StatusCode::OK, &st);
    t.check(
        "version field",
        &json!(mmap_model::telemetry::crate_version()),
        &body["version"],
    );

    t.step(
        "http",
        "POST /files f (2 pages), map shared, write page0 = 0xAB",
    );
    let (st, _) = api
        .post("/files", json!({"path": "f", "size": 2 * PAGE}))
        .await;
    t.check("create status", &StatusCode::OK, &st);
    let (st, body) = api
        .post(
            "/mappings",
            json!({"path": "f", "offset": 0, "length": 2 * PAGE, "kind": "shared"}),
        )
        .await;
    t.check("map status", &StatusCode::OK, &st);
    let map_id = body["mapping_id"].as_u64().unwrap();
    let (st, _) = api
        .post(
            &format!("/mappings/{map_id}/write"),
            json!({"offset": 0, "data_hex": hex::encode(vec![0xABu8; PAGE as usize])}),
        )
        .await;
    t.check("write status", &StatusCode::NO_CONTENT, &st);

    t.step("http", "GET /files/state shows dirty page 0");
    let (st, body) = api.get("/files/state?path=f").await;
    t.check("state status", &StatusCode::OK, &st);
    t.check("dirty pages", &json!([0]), &body["dirty_pages"]);

    t.step(
        "http",
        "inject store failure: sync -> 500 sync_failed, dirty kept",
    );
    api.faults.arm_write_failures(1);
    let (st, body) = api
        .post(&format!("/mappings/{map_id}/sync"), json!({}))
        .await;
    t.check("sync status", &StatusCode::INTERNAL_SERVER_ERROR, &st);
    t.check(
        "sync error category",
        &json!("sync_failed"),
        &body["error"]["category"],
    );
    t.check(
        "sync failed pages",
        &json!([0]),
        &body["error"]["failed_pages"],
    );
    let (_, body) = api.get("/files/state?path=f").await;
    t.check(
        "dirty kept after failure",
        &json!([0]),
        &body["dirty_pages"],
    );

    t.step(
        "http",
        "retry sync -> 200, persistent image readable via /files/content",
    );
    let (st, body) = api
        .post(&format!("/mappings/{map_id}/sync"), json!({}))
        .await;
    t.check("retry status", &StatusCode::OK, &st);
    t.check("persisted", &json!([0]), &body["persisted"]);
    let (_, body) = api
        .get(&format!("/files/content?path=f&offset=0&length={PAGE}"))
        .await;
    t.check(
        "persistent image",
        &hex::encode(vec![0xABu8; PAGE as usize]),
        body["data_hex"].as_str().unwrap(),
    );

    t.step(
        "http",
        "truncate to half a page, then read page 1 -> 409 access_out_of_range",
    );
    let (st, body) = api
        .post("/files/truncate", json!({"path": "f", "size": PAGE / 2}))
        .await;
    t.check("truncate status", &StatusCode::OK, &st);
    t.check("zeroed tail", &json!(0), &body["zeroed_tail_page"]);
    let (st, body) = api
        .post(
            &format!("/mappings/{map_id}/read"),
            json!({"offset": PAGE, "length": 1}),
        )
        .await;
    t.check("read status", &StatusCode::CONFLICT, &st);
    t.check(
        "read error category",
        &json!("access_out_of_range"),
        &body["error"]["category"],
    );

    t.step(
        "http",
        "unaligned map offset -> 400 invalid_argument; unknown file -> 404",
    );
    let (st, body) = api
        .post(
            "/mappings",
            json!({"path": "f", "offset": 7, "length": PAGE, "kind": "shared"}),
        )
        .await;
    t.check("unaligned status", &StatusCode::BAD_REQUEST, &st);
    t.check(
        "unaligned category",
        &json!("invalid_argument"),
        &body["error"]["category"],
    );
    let (st, body) = api.get("/files/state?path=nope").await;
    t.check("missing file status", &StatusCode::NOT_FOUND, &st);
    t.check(
        "missing file category",
        &json!("not_found"),
        &body["error"]["category"],
    );

    t.step(
        "http",
        "DELETE /mappings/{id} unmaps; stats reflect the run",
    );
    let (st, _) = api.delete(&format!("/mappings/{map_id}")).await;
    t.check("unmap status", &StatusCode::OK, &st);
    let (_, body) = api.get("/stats").await;
    t.check("sync failures counted", &json!(1), &body["sync_failures"]);
    t.check("sigbus counted", &json!(1), &body["sigbus_errors"]);
    t.step("done", "HTTP end-to-end verified");
}
