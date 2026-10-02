//! Shared integration-test helpers: a dependency-free HTTP/1.1 client and
//! server harness, plus multiset comparison against the independent oracle.
#![allow(dead_code)]

use std::io::{Read, Write};
use std::net::TcpStream;
use std::time::Duration;

use serde_json::Value;

/// A started in-process server bound to an ephemeral port.
pub struct Server {
    pub addr: String,
}

impl Server {
    /// Build the app and serve it on a background thread on 127.0.0.1:0.
    pub fn start(data_dir: &std::path::Path) -> Server {
        let (tx, rx) = std::sync::mpsc::channel();
        let data = data_dir.to_path_buf();
        std::thread::spawn(move || {
            let rt = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .unwrap();
            rt.block_on(async move {
                // Bind inside the runtime on a native async socket (Tokio 1.x
                // rejects registering an externally created blocking socket).
                let io = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
                let addr = io.local_addr().unwrap().to_string();
                tx.send(addr).unwrap();

                let root = data.join("runs");
                std::fs::create_dir_all(&root).unwrap();
                setops::runlog::init_run_root(root).ok();
                let app = setops::service::app(data.join("runs"), tight_budget());
                axum::serve(io, app).await.unwrap();
            });
        });
        let addr = rx.recv().expect("server address").to_string();
        Server { addr }
    }

    pub fn request(&self, method: &str, path: &str, body: &str) -> (u16, Value) {
        let mut stream = TcpStream::connect(self.addr.as_str()).expect("connect");
        stream
            .set_read_timeout(Some(Duration::from_secs(20)))
            .unwrap();
        let req = format!(
            "{method} {path} HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n\
             Content-Length: {len}\r\nConnection: close\r\n\r\n{body}",
            len = body.len()
        );
        stream.write_all(req.as_bytes()).unwrap();
        let mut raw = Vec::new();
        stream.read_to_end(&mut raw).unwrap();
        parse_http(&raw)
    }
}

fn tight_budget() -> setops::resource::Budget {
    setops::resource::Budget {
        memory_bytes: 4096,
        partition_buffer_bytes: 256,
        partition_table_bytes: 512,
        spill_bytes: 64 * 1024 * 1024,
        spill_files: 100_000,
        output_buffer_rows: 16,
    }
}

/// Minimal HTTP/1.1 response parser (handles Content-Length and chunked).
fn parse_http(raw: &[u8]) -> (u16, Value) {
    let header_end = find_subslice(raw, b"\r\n\r\n").expect("headers");
    let head = std::str::from_utf8(&raw[..header_end]).unwrap();
    let status: u16 = head
        .lines()
        .next()
        .and_then(|l| l.split_whitespace().nth(1))
        .and_then(|s| s.parse().ok())
        .unwrap_or(0);

    let mut body_bytes: &[u8] = &raw[header_end + 4..];
    let dechunked;
    let lower = head.to_ascii_lowercase();
    if lower.contains("transfer-encoding: chunked") {
        dechunked = dechunk(body_bytes);
        body_bytes = &dechunked;
    }
    let text = std::str::from_utf8(body_bytes).unwrap_or("");
    let json = serde_json::from_str(text).unwrap_or(Value::Null);
    (status, json)
}

fn dechunk(input: &[u8]) -> Vec<u8> {
    let mut out = Vec::new();
    let mut pos = 0;
    while let Some(rel) = find_subslice(&input[pos..], b"\r\n") {
        let line_end = pos + rel;
        let size_str = std::str::from_utf8(&input[pos..line_end]).unwrap().trim();
        let size = usize::from_str_radix(size_str.split(';').next().unwrap(), 16).unwrap();
        pos = line_end + 2;
        if size == 0 {
            break;
        }
        out.extend_from_slice(&input[pos..pos + size]);
        pos += size + 2;
    }
    out
}

fn find_subslice(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack.windows(needle.len()).position(|w| w == needle)
}

/// Standard two-column schema shared by most fixtures.
pub fn schema_json() -> Value {
    serde_json::json!({
        "fields": [
            {"name": "id", "data_type": {"kind": "int64"}, "nullable": true},
            {"name": "tag", "data_type": {"kind": "utf8"}, "nullable": true}
        ]
    })
}

/// Nested schema exercising list/struct NULLs.
pub fn nested_schema_json() -> Value {
    serde_json::json!({
        "fields": [
            {"name": "g", "data_type": {"kind": "list", "name": "e",
                "data_type": {"kind": "utf8"}, "nullable": true}, "nullable": true},
            {"name": "m", "data_type": {"kind": "struct", "fields": [
                {"name": "flag", "data_type": {"kind": "bool"}, "nullable": true}
            ]}, "nullable": true}
        ]
    })
}

pub fn execute_body(
    op: &str,
    quantifier: &str,
    left: &[Value],
    right: &[Value],
    budget_extra: Value,
) -> String {
    let mut body = serde_json::json!({
        "schema": schema_json(),
        "op": op,
        "quantifier": quantifier,
        "left_batches": [left],
        "right_batches": [right],
    });
    if let Some(b) = budget_extra.as_object() {
        body.as_object_mut()
            .unwrap()
            .insert("budget".into(), Value::Object(b.clone()));
    }
    body.to_string()
}

/// Flatten all result fragments of a finished run into one JSON row vector.
pub fn fetch_all_rows(server: &Server, run_id: &str, fragment_count: usize) -> Vec<Value> {
    let mut rows = Vec::new();
    for i in 0..fragment_count {
        let (status, body) =
            server.request("GET", &format!("/runs/{run_id}/results?fragment={i}"), "");
        assert_eq!(status, 200, "fragment {i}: {body}");
        rows.extend(body["rows"].as_array().unwrap().clone());
    }
    rows
}

/// Compare an engine JSON result row-for-row (as a multiset) with expected
/// JSON rows using the independent reference multiset semantics.
pub fn assert_multiset_eq(got: &[Value], expected: &[Value], context: &str) {
    let g = multiset(got);
    let e = multiset(expected);
    assert_eq!(g, e, "{context}\nmultiset mismatch");
}

fn multiset(rows: &[Value]) -> std::collections::BTreeMap<String, u64> {
    let mut m = std::collections::BTreeMap::new();
    for r in rows {
        *m.entry(r.to_string()).or_insert(0) += 1;
    }
    m
}
