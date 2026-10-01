//! End-to-end HTTP tests: a real Axum server on an ephemeral port, real JSON
//! over the wire, and assertions on concrete result sets AND concrete error
//! codes. Unknown/error states must never come back as success.

mod common;

use std::net::SocketAddr;
use std::sync::{Arc, RwLock};

use common::init_tracing;
use serde_json::{json, Value};
use tribool_index::api::{router, AppState};
use tribool_index::config::Config;
use tribool_index::state::Catalog;

struct TestServer {
    addr: SocketAddr,
    shutdown: Option<tokio::sync::oneshot::Sender<()>>,
    handle: Option<std::thread::JoinHandle<()>>,
}

impl TestServer {
    fn start() -> Self {
        // Bind a std listener first to reserve an ephemeral port, then hand it
        // to a Tokio listener INSIDE the runtime (from_std needs a reactor).
        let std_listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        std_listener.set_nonblocking(true).unwrap();
        let addr = std_listener.local_addr().unwrap();

        let (tx, rx) = tokio::sync::oneshot::channel::<()>();
        let handle = std::thread::spawn(move || {
            let rt = tokio::runtime::Builder::new_multi_thread()
                .enable_all()
                .build()
                .unwrap();
            rt.block_on(async move {
                let listener = tokio::net::TcpListener::from_std(std_listener).unwrap();
                let state = AppState {
                    catalog: Arc::new(RwLock::new(Catalog::new())),
                    config: Arc::new(Config::default_values()),
                };
                let server =
                    axum::serve(listener, router(state)).with_graceful_shutdown(async move {
                        let _ = rx.await;
                    });
                server.await.unwrap();
            });
        });
        // The port is already bound by the std socket (now owned by the
        // thread), so polling the readiness is just waiting for accept() to
        // start spinning up.
        std::thread::sleep(std::time::Duration::from_millis(150));
        TestServer {
            addr,
            shutdown: Some(tx),
            handle: Some(handle),
        }
    }

    fn url(&self, path: &str) -> String {
        format!("http://{}{path}", self.addr)
    }
}

impl Drop for TestServer {
    fn drop(&mut self) {
        if let Some(tx) = self.shutdown.take() {
            let _ = tx.send(());
        }
        if let Some(h) = self.handle.take() {
            let _ = h.join();
        }
    }
}

fn client() -> reqwest::blocking::Client {
    reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .build()
        .unwrap()
}

#[test]
fn full_query_lifecycle_over_http_with_three_value_counts() {
    init_tracing();
    let server = TestServer::start();
    let c = client();
    let run = "e2e-lifecycle";

    // Create with 13 rows (non-multiple-of-8), including NULLs.
    let rows: Vec<Value> = (0..13)
        .map(|i| {
            json!({
                "city": if i % 2 == 0 { "BJ" } else { "SH" },
                "age": if i % 4 == 1 { Value::Null } else { json!(20 + i as i64) },
                "active": if i % 3 == 2 { Value::Null } else { json!(i % 2 == 0) },
            })
        })
        .collect();
    let resp = c
        .post(server.url("/tables"))
        .json(&json!({
            "name": "people",
            "columns": [
                {"name": "city", "type": "text"},
                {"name": "age", "type": "int"},
                {"name": "active", "type": "bool"}
            ],
            "rows": rows
        }))
        .send()
        .unwrap();
    assert_eq!(resp.status(), 200);
    let body: Value = resp.json().unwrap();
    assert_eq!(body["ok"], true);
    assert_eq!(body["version"]["version"], 1);
    assert_eq!(body["version"]["total_rows"], 13);

    // Query: age >= 25 AND active = true — NULL crossings expected.
    let filter = json!({
        "op": "and",
        "args": [
            {"op": "cmp", "column": "age", "cmp": ">=", "value": 25},
            {"op": "cmp", "column": "active", "cmp": "=", "value": true}
        ]
    });
    let resp = c
        .post(server.url("/tables/people/query"))
        .json(&json!({ "filter": filter }))
        .send()
        .unwrap();
    assert_eq!(resp.status(), 200);
    let body: Value = resp.json().unwrap();
    assert_eq!(body["ok"], true);
    assert_eq!(body["version"], 1);
    let counts = &body["counts"];
    let t = counts["true"].as_u64().unwrap();
    let f = counts["false"].as_u64().unwrap();
    let u = counts["unknown"].as_u64().unwrap();
    assert_eq!(t + f + u, 13, "exact partition over the alive universe");
    // Independent expected rows (compute directly from the fixture pattern):
    let mut exp_t = 0u64;
    let mut exp_f = 0u64;
    let mut exp_u = 0u64;
    for i in 0..13i64 {
        let age_null = i % 4 == 1;
        let age_ge = 20 + i >= 25;
        let active_null = i % 3 == 2;
        let active_true = i % 2 == 0;
        let age = if age_null {
            common::Ora::U
        } else if age_ge {
            common::Ora::T
        } else {
            common::Ora::F
        };
        let act = if active_null {
            common::Ora::U
        } else if active_true {
            common::Ora::T
        } else {
            common::Ora::F
        };
        match common::o_and(age, act) {
            common::Ora::T => exp_t += 1,
            common::Ora::F => exp_f += 1,
            common::Ora::U => exp_u += 1,
        }
    }
    assert_eq!(t, exp_t, "TRUE mismatch");
    assert_eq!(f, exp_f, "FALSE mismatch");
    assert_eq!(u, exp_u, "UNKNOWN mismatch");
    assert!(u > 0, "fixture must produce UNKNOWN rows");
    // matched_ids must equal the TRUE set and be sorted indices.
    let ids: Vec<u64> = body["matched_ids"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_u64().unwrap())
        .collect();
    assert_eq!(ids.len() as u64, t);
    assert!(ids.windows(2).all(|w| w[0] < w[1]));
    // Trace is present and references each step with cardinalities.
    let trace = body["trace"].as_array().unwrap();
    assert!(trace.len() >= 3, "two leaves + AND node");
    let last = trace.last().unwrap();
    assert_eq!(last["true_rows"], json!(t));
    assert!(last["basis"].as_str().unwrap().contains("TRUE"));
    tracing::info!(
        run,
        true_cnt = t,
        false_cnt = f,
        unknown_cnt = u,
        "HTTP query partition verified"
    );

    // Delete two rows, version must bump, they must vanish from results.
    let resp = c
        .post(server.url("/tables/people/delete"))
        .json(&json!({"ids": [0, 12]}))
        .send()
        .unwrap();
    assert_eq!(resp.status(), 200);
    let body: Value = resp.json().unwrap();
    assert_eq!(body["version"]["version"], 2);
    assert_eq!(body["version"]["alive_rows"], 11);

    let resp = c
        .post(server.url("/tables/people/query"))
        .json(&json!({ "filter": { "op": "cmp", "column": "age", "cmp": "is_not_null" } }))
        .send()
        .unwrap();
    let body: Value = resp.json().unwrap();
    assert_eq!(body["version"], 2);
    let counts = &body["counts"];
    assert_eq!(
        counts["true"].as_u64().unwrap() + counts["false"].as_u64().unwrap(),
        11
    );
    assert_eq!(counts["unknown"], 0);
}

#[test]
fn http_error_codes_are_concrete_and_never_reported_ok() {
    init_tracing();
    let server = TestServer::start();
    let c = client();

    // 404 unknown table.
    let resp = c
        .post(server.url("/tables/ghost/query"))
        .json(&json!({"filter": {"op": "cmp", "column": "x", "cmp": "=", "value": 1}}))
        .send()
        .unwrap();
    assert_eq!(resp.status(), 404);
    let body: Value = resp.json().unwrap();
    assert_eq!(body["ok"], false);
    assert_eq!(body["error"]["code"], "NOT_FOUND");
    assert!(body["run_id"].is_string());

    // Create table for the remaining negative cases.
    c.post(server.url("/tables"))
        .json(&json!({
            "name": "t",
            "columns": [{"name": "age", "type": "int"}],
            "rows": [{"age": 1}, {"age": null}]
        }))
        .send()
        .unwrap();

    // Type mismatch -> 400 TYPE_MISMATCH.
    let resp = c
        .post(server.url("/tables/t/query"))
        .json(&json!({"filter": {"op": "cmp", "column": "age", "cmp": "=", "value": "string"}}))
        .send()
        .unwrap();
    assert_eq!(resp.status(), 400);
    let body: Value = resp.json().unwrap();
    assert_eq!(body["error"]["code"], "TYPE_MISMATCH");

    // Unknown operator -> 400 UNSUPPORTED_OPERATOR.
    let resp = c
        .post(server.url("/tables/t/query"))
        .json(&json!({"filter": {"op": "cmp", "column": "age", "cmp": "~~", "value": 1}}))
        .send()
        .unwrap();
    assert_eq!(resp.status(), 400);
    let body: Value = resp.json().unwrap();
    assert_eq!(body["error"]["code"], "UNSUPPORTED_OPERATOR");

    // is_null with a value -> 400 INVALID_INPUT (not silently ignored).
    let resp = c
        .post(server.url("/tables/t/query"))
        .json(&json!({"filter": {"op": "cmp", "column": "age", "cmp": "is_null", "value": 1}}))
        .send()
        .unwrap();
    assert_eq!(resp.status(), 400);
    let body: Value = resp.json().unwrap();
    assert_eq!(body["error"]["code"], "INVALID_INPUT");

    // Out-of-range delete -> 400 INVALID_INPUT.
    let resp = c
        .post(server.url("/tables/t/delete"))
        .json(&json!({"ids": [99]}))
        .send()
        .unwrap();
    assert_eq!(resp.status(), 400);
    let body: Value = resp.json().unwrap();
    assert_eq!(body["error"]["code"], "INVALID_INPUT");

    // Bad JSON shape -> framework 422, still not an ok response.
    let resp = c
        .post(server.url("/tables"))
        .header("content-type", "application/json")
        .body("{not json")
        .send()
        .unwrap();
    assert!(resp.status().is_client_error());

    // Health still works.
    let resp = c.get(server.url("/health")).send().unwrap();
    let body: Value = resp.json().unwrap();
    assert_eq!(body["status"], "ok");
}

#[test]
fn append_grows_universe_and_reindexes_over_http() {
    init_tracing();
    let server = TestServer::start();
    let c = client();
    c.post(server.url("/tables"))
        .json(&json!({
            "name": "t",
            "columns": [{"name": "age", "type": "int"}],
            "rows": [{"age": 5}, {"age": null}, {"age": 40}]
        }))
        .send()
        .unwrap();
    c.post(server.url("/tables/t/delete"))
        .json(&json!({"ids": [0]}))
        .send()
        .unwrap();
    let resp = c
        .post(server.url("/tables/t/rows"))
        .json(&json!({"rows": [{"age": 40}, {"age": 40}]}))
        .send()
        .unwrap();
    assert_eq!(resp.status(), 200);
    let body: Value = resp.json().unwrap();
    assert_eq!(
        body["version"]["version"], 3,
        "create v1, delete v2, append v3"
    );
    assert_eq!(body["version"]["total_rows"], 5);
    assert_eq!(
        body["version"]["deleted_rows"], 1,
        "delete mask survives append"
    );

    let resp = c
        .post(server.url("/tables/t/query"))
        .json(&json!({"filter": {"op": "cmp", "column": "age", "cmp": "=", "value": 40}}))
        .send()
        .unwrap();
    let body: Value = resp.json().unwrap();
    // Rows 3,4 match; row 2 also matches (alive); row 0 (40? no, age 5) — so 2,3,4.
    let ids: Vec<u64> = body["matched_ids"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_u64().unwrap())
        .collect();
    assert_eq!(ids, vec![2, 3, 4]);
    assert_eq!(body["counts"]["unknown"], 1, "row 1 NULL stays UNKNOWN");
}
