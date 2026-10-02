//! End-to-end tests driven over real HTTP against an in-process Axum server.
//!
//! Expected answers are produced by an *independent* multiset computation in
//! this file (plain `BTreeMap` over canonical JSON), never by the engine under
//! test. Tests assert concrete result multisets, concrete error
//! kind/code/status, and on-disk replay artifacts.

mod common;

use std::collections::BTreeMap;

use common::*;
use serde_json::{Value, json};

/// Independent multiset over JSON rows (does not use the crate's encoding).
fn counts(rows: &[Value]) -> BTreeMap<String, u64> {
    let mut m = BTreeMap::new();
    for r in rows {
        *m.entry(r.to_string()).or_insert(0u64) += 1;
    }
    m
}

/// Independent six-operation oracle returning the expected output multiset.
fn expected(op: &str, quantifier: &str, left: &[Value], right: &[Value]) -> BTreeMap<String, u64> {
    let l = counts(left);
    let r = counts(right);
    let mut keys: std::collections::BTreeSet<String> = l.keys().cloned().collect();
    keys.extend(r.keys().cloned());
    let mut out = BTreeMap::new();
    for k in keys {
        let a = l.get(&k).copied().unwrap_or(0);
        let b = r.get(&k).copied().unwrap_or(0);
        let n = match (op, quantifier) {
            ("union", "all") => a + b,
            ("union", "distinct") => u64::from(a + b > 0),
            ("intersect", "all") => a.min(b),
            ("intersect", "distinct") => u64::from(a > 0 && b > 0),
            ("except", "all") => a.saturating_sub(b),
            ("except", "distinct") => u64::from(a > 0 && b == 0),
            _ => panic!("bad op {op}/{quantifier}"),
        };
        if n > 0 {
            out.insert(k, n);
        }
    }
    out
}

fn result_multiset(rows: &[Value]) -> BTreeMap<String, u64> {
    counts(rows)
}

fn sample_inputs() -> (Vec<Value>, Vec<Value>) {
    // Repeated rows, NULLs, empty string, marker-byte strings, type "1" vs 1.
    let left = vec![
        json!([1, "a"]),
        json!([1, "a"]),
        json!([2, "b"]),
        json!([null, "n"]),
        json!([3, ""]),
        json!([5, "1"]),
        json!([6, "xþÿy"]),
        json!([7, "\u{fe}\x03\x00\x00\x00\x00\x00\x00\x00\x01"]),
        json!([8, "c1þc2"]),
        json!([1, "a"]),
        json!([9, null]),
    ];
    let right = vec![
        json!([1, "a"]),
        json!([2, "b"]),
        json!([2, "b"]),
        json!([null, "n"]),
        json!([3, ""]),
        json!([5, "1"]),
        json!([10, "z"]),
        json!([9, null]),
        json!([11, "dup"]),
        json!([11, "dup"]),
        json!([12, "a"]),
    ];
    (left, right)
}

#[test]
fn all_six_operations_match_independent_oracle_over_http() {
    let dir = tempfile::tempdir().unwrap();
    let server = Server::start(dir.path());
    let (left, right) = sample_inputs();

    for op in ["union", "intersect", "except"] {
        for quantifier in ["all", "distinct"] {
            let body = execute_body(op, quantifier, &left, &right, Value::Null);
            let (status, resp) = server.request("POST", "/execute", &body);
            assert_eq!(status, 200, "{op}/{quantifier} -> {resp}");
            let got = resp["rows"].as_array().unwrap();
            // The inline field returns only fragment 0; for these small inputs
            // everything must fit there (fragment_count == 1).
            assert_eq!(resp["result_fragments"], 1, "{op}/{quantifier}");
            assert_multiset_eq(
                got,
                &expected_keys(&expected(op, quantifier, &left, &right)),
                &format!("{op}/{quantifier}"),
            );
        }
    }
}

fn expected_keys(m: &BTreeMap<String, u64>) -> Vec<Value> {
    let mut v = Vec::new();
    for (k, n) in m {
        for _ in 0..*n {
            v.push(serde_json::from_str(k).unwrap());
        }
    }
    v
}

#[test]
fn identical_multiset_result_in_memory_and_forced_external_paths() {
    // The logical answer must not depend on how much spilling happens.
    let dir = tempfile::tempdir().unwrap();
    let server = Server::start(dir.path());
    let (left, right) = sample_inputs();
    let want = expected("union", "all", &left, &right);

    // Tight buffer/table forces many spills and recursive splits.
    let forced = json!({
        "memory_bytes": 512,
        "partition_buffer_bytes": 64,
        "partition_table_bytes": 128,
        "spill_bytes": 67108864,
        "spill_files": 100000,
        "output_buffer_rows": 8
    });
    let body = execute_body("union", "all", &left, &right, forced);
    let (status, resp) = server.request("POST", "/execute", &body);
    assert_eq!(status, 200, "{resp}");
    assert!(
        resp["stats"]["spill_segments"].as_u64().unwrap() >= 1,
        "should spill: {resp}"
    );

    // Collect all fragments.
    let frags = resp["result_fragments"].as_u64().unwrap() as usize;
    let run_id = resp["run_id"].as_str().unwrap();
    let mut got = Vec::new();
    for i in 0..frags {
        let (s, b) = server.request("GET", &format!("/runs/{run_id}/results?fragment={i}"), "");
        assert_eq!(s, 200);
        got.extend(b["rows"].as_array().unwrap().clone());
    }
    assert_eq!(result_multiset(&got), want);
}

#[test]
fn streaming_run_lifecycle_and_larger_than_memory_input() {
    let dir = tempfile::tempdir().unwrap();
    let server = Server::start(dir.path());

    // Create run.
    let create = json!({
        "run_id": "stream-big",
        "schema": schema_json(),
        "op": "except",
        "quantifier": "all"
    })
    .to_string();
    let (status, resp) = server.request("POST", "/runs", &create);
    assert_eq!(status, 200, "{resp}");
    assert_eq!(resp["state"], "created");
    assert!(resp["fanout"].as_u64().unwrap() >= 2);

    // Independent expected multisets accumulated from generated rows.
    let mut want_left: Vec<Value> = Vec::new();
    let mut want_right: Vec<Value> = Vec::new();

    // Stream many batches that far exceed the 256-byte buffers.
    let batches = 40;
    for b in 0..batches {
        let mut lrows = Vec::new();
        let mut rrows = Vec::new();
        for i in 0..120i64 {
            let key = (b as i64) * 1000 + i;
            let row = json!([key, format!("v{key}")]);
            lrows.push(row.clone());
            want_left.push(row.clone());
            if i % 3 == 0 {
                // right contains 1/3 of the keys once; left has them once too
                // => those vanish under EXCEPT ALL, rest survive.
                rrows.push(row.clone());
                want_right.push(row);
            }
        }
        for side in ["left", "right"] {
            let rows = if side == "left" { &lrows } else { &rrows };
            let body = json!({ "rows": rows }).to_string();
            let path = format!("/runs/stream-big/ingest/{side}");
            let (s, r) = server.request("POST", &path, &body);
            assert_eq!(s, 200, "ingest {side}: {r}");
        }
    }

    let (status, run) = server.request("POST", "/runs/stream-big/execute", "");
    assert_eq!(status, 200, "{run}");
    assert_eq!(run["state"], "succeeded");
    assert!(
        run["stats"]["spill_segments"].as_u64().unwrap() > 5,
        "large input should spill heavily: {}",
        run["stats"]
    );

    let frags = run["result_fragments"].as_u64().unwrap() as usize;
    let got = fetch_all_rows(&server, "stream-big", frags);
    assert_multiset_eq(
        &got,
        &expected_keys(&expected("except", "all", &want_left, &want_right)),
        "stream except all",
    );

    // Status reflects lifecycle.
    let (s, status_body) = server.request("GET", "/runs/stream-big", "");
    assert_eq!(s, 200);
    assert_eq!(status_body["state"], "succeeded");
}

#[test]
fn input_errors_are_classified_as_400_with_codes() {
    let dir = tempfile::tempdir().unwrap();
    let server = Server::start(dir.path());

    // Malformed JSON.
    let (s, b) = server.request("POST", "/execute", "{ not json");
    assert_eq!(s, 400);
    assert_eq!(b["error"]["kind"], "input");
    assert_eq!(b["error"]["code"], "invalid_json");

    // Wrong type for a column (string into int64).
    let body = json!({
        "schema": schema_json(),
        "op": "union", "quantifier": "all",
        "left_batches": [[[1, "a"]]],
        "right_batches": [[["nope", "a"]]]
    })
    .to_string();
    let (s, b) = server.request("POST", "/execute", &body);
    assert_eq!(s, 400, "{b}");
    assert_eq!(b["error"]["kind"], "input");
    assert_eq!(b["error"]["code"], "type_mismatch");
    assert!(b["error"]["message"].as_str().unwrap().contains("row 0"));

    // Row arity mismatch.
    let body = json!({
        "schema": schema_json(),
        "op": "union", "quantifier": "all",
        "left_batches": [[[1]]],
        "right_batches": [[[2, "b"]]]
    })
    .to_string();
    let (s, b) = server.request("POST", "/execute", &body);
    assert_eq!(s, 400);
    assert_eq!(b["error"]["code"], "row_arity");

    // Unknown operator.
    let body = json!({
        "schema": schema_json(),
        "op": "difference", "quantifier": "all",
        "left_batches": [[[1, "a"]]],
        "right_batches": [[[2, "b"]]]
    })
    .to_string();
    let (s, _b) = server.request("POST", "/execute", &body);
    assert_eq!(s, 400);

    // Bad side path.
    let create =
        json!({"run_id":"r-badside","schema":schema_json(),"op":"union","quantifier":"all"})
            .to_string();
    let (s, _) = server.request("POST", "/runs", &create);
    assert_eq!(s, 200);
    let (s, b) = server.request(
        "POST",
        "/runs/r-badside/ingest/middle",
        &json!({"rows":[]}).to_string(),
    );
    assert_eq!(s, 400);
    assert_eq!(b["error"]["code"], "bad_side");
}

#[test]
fn state_conflicts_are_409() {
    let dir = tempfile::tempdir().unwrap();
    let server = Server::start(dir.path());

    let create = json!({"run_id":"r-state","schema":schema_json(),"op":"union","quantifier":"all"})
        .to_string();
    let (s, _) = server.request("POST", "/runs", &create);
    assert_eq!(s, 200);

    // Results before execute -> state conflict.
    let (s, b) = server.request("GET", "/runs/r-state/results?fragment=0", "");
    assert_eq!(s, 409, "{b}");
    assert_eq!(b["error"]["code"], "not_finalized");

    // Execute empty run succeeds (empty result).
    let (s, b) = server.request("POST", "/runs/r-state/execute", "");
    assert_eq!(s, 200, "{b}");

    // Ingest after finalize -> 409.
    let (s, b) = server.request(
        "POST",
        "/runs/r-state/ingest/left",
        &json!({"rows": [[1, "a"]]}).to_string(),
    );
    assert_eq!(s, 409);
    assert_eq!(b["error"]["kind"], "state_conflict");

    // Duplicate run_id -> 409 run_exists.
    let (s, b) = server.request("POST", "/runs", &create);
    assert_eq!(s, 409, "{b}");
    assert_eq!(b["error"]["code"], "run_exists");
}

#[test]
fn resource_exhaustion_is_507_and_distinct_from_other_errors() {
    let dir = tempfile::tempdir().unwrap();
    let server = Server::start(dir.path());
    let (left, right) = sample_inputs();

    // Extremely small spill byte budget -> cannot even write one segment.
    let body = execute_body("union", "all", &left, &right, json!({ "spill_bytes": 8 }));
    let (s, b) = server.request("POST", "/execute", &body);
    assert_eq!(s, 507, "expected insufficient storage, got {b}");
    assert_eq!(b["error"]["kind"], "resource_exhausted");
    assert_eq!(b["error"]["code"], "spill_byte_limit");

    // Single key larger than the table budget is irreducible.
    let body = execute_body(
        "union",
        "distinct",
        &[json!([1, "abcdefghijklmnop"])],
        &[json!([2, "qrstuvwxyz012345"])],
        json!({
            "memory_bytes": 256,
            "partition_buffer_bytes": 64,
            "partition_table_bytes": 8
        }),
    );
    let (s, b) = server.request("POST", "/execute", &body);
    assert_eq!(s, 507, "{b}");
    assert_eq!(b["error"]["code"], "partition_key_too_large");
}

#[test]
fn unknown_run_is_404() {
    let dir = tempfile::tempdir().unwrap();
    let server = Server::start(dir.path());
    let (s, b) = server.request("GET", "/runs/does-not-exist", "");
    assert_eq!(s, 404);
    assert_eq!(b["error"]["code"], "unknown_run");
}

#[test]
fn run_log_is_replayable_and_records_key_state() {
    let dir = tempfile::tempdir().unwrap();
    let server = Server::start(dir.path());
    let (left, right) = sample_inputs();
    let mut body = serde_json::json!({
        "run_id": "logged-run",
        "schema": schema_json(),
        "op": "intersect", "quantifier": "all",
        "left_batches": [left], "right_batches": [right]
    });
    body.as_object_mut().unwrap().insert(
        "budget".into(),
        json!({"partition_buffer_bytes": 64, "partition_table_bytes": 128, "memory_bytes": 512}),
    );
    let (s, resp) = server.request("POST", "/execute", &body.to_string());
    assert_eq!(s, 200, "{resp}");

    let (s, log) = server.request("GET", "/runs/logged-run/log", "");
    assert_eq!(s, 200);
    let text = log.as_str().map(|s| s.to_owned()).unwrap_or_else(|| {
        // log endpoint returns raw text; our client falls back to Null if not
        // JSON, so instead read the file from disk.
        String::new()
    });
    // The /log endpoint returns plain text (not JSON); verify the on-disk file.
    let log_path = dir.path().join("runs/logged-run/run.jsonl");
    let disk = std::fs::read_to_string(log_path).unwrap();
    let combined = format!("{text}{disk}");
    assert!(combined.contains("logged-run"));
    assert!(combined.contains("run_created"));
    assert!(combined.contains("run_started"));
    assert!(combined.contains("run_succeeded"));
    assert!(
        combined.contains("recursive")
            || combined.contains("fanout")
            || combined.contains("partition")
    );
    // Every line is valid JSON with a monotonic step and run id.
    let mut last_step = 0u64;
    for line in disk.lines() {
        let ev: Value = serde_json::from_str(line).expect("log line is JSON");
        assert_eq!(ev["run_id"], "logged-run");
        let step = ev["step"].as_u64().unwrap();
        assert!(step > last_step, "steps must be monotonic");
        last_step = step;
    }
    // Raw request retained for replay.
    assert!(dir.path().join("runs/logged-run/request.json").exists());
}

#[test]
fn nested_nulls_and_collision_strings_are_handled() {
    let dir = tempfile::tempdir().unwrap();
    let server = Server::start(dir.path());

    let body = json!({
        "schema": nested_schema_json(),
        "op": "except", "quantifier": "distinct",
        "left_batches": [[
            [["a", null, ""], {"flag": true}],
            [null, null],
            [[], {"flag": null}],
            [[null, null], {"flag": false}],
            [["a"], {"flag": true}],
            [["þÿ", ""], {"flag": true}]
        ]],
        "right_batches": [[
            [["a", null, ""], {"flag": true}],
            [null, null],
            [[null], {"flag": false}],
            [["a"], {"flag": true}]
        ]]
    })
    .to_string();
    let (s, b) = server.request("POST", "/execute", &body);
    assert_eq!(s, 200, "{b}");

    let got = b["rows"].as_array().unwrap();
    let got_ms = counts(got);
    let expect_ms = expected(
        "except",
        "distinct",
        &[
            json!([["a", null, ""], {"flag": true}]),
            json!([null, null]),
            json!([[], {"flag": null}]),
            json!([[null, null], {"flag": false}]),
            json!([["a"], {"flag": true}]),
            json!([["þÿ", ""], {"flag": true}]),
        ],
        &[
            json!([["a", null, ""], {"flag": true}]),
            json!([null, null]),
            json!([[null], {"flag": false}]),
            json!([["a"], {"flag": true}]),
        ],
    );
    assert_eq!(
        got_ms, expect_ms,
        "nested NULL except distinct mismatch: {got_ms:?}"
    );
    assert_eq!(got.len(), 3, "three distinct rows should remain: {got:?}");
}
