//! Local, offline validation walkthrough (no HTTP required):
//! runs the triangle and skewed sparse-intersection queries through the same
//! validation entry point as the server, prints the decision, the concrete
//! result rows, the Leapfrog counters and the independent naive-oracle
//! counters, so the "no Cartesian intermediate" claim is directly auditable.
//!
//! Run: `cargo run --example run_validation`

use std::sync::Arc;

use leapfrog_triejoin::config::Config;
use leapfrog_triejoin::state::AppState;
use leapfrog_triejoin::validate::{execute, parse_value, QueryRequest, ValidateDecision};

fn run_case(title: &str, request: serde_json::Value, state: &AppState) {
    println!("=== {title} ===");
    let request: QueryRequest = parse_value(request).expect("fixture request parses");
    match execute(&Config::default(), state, request) {
        ValidateDecision::Ran(payload) => {
            let outcome = &payload.outcome;
            let record = &payload.record;
            println!(
                "decision: {:?} (request_id={}, stop={:?})",
                outcome.decision, outcome.request_id, outcome.stats.stop_reason
            );
            for reason in &outcome.reasons {
                println!("  reason [{}]: {}", reason.code, reason.detail);
            }
            println!(
                "columns: {:?}",
                outcome.columns.iter().map(|c| &c.name).collect::<Vec<_>>()
            );
            for row in &outcome.rows {
                println!("  row: {row:?}");
            }
            println!(
                "lftj: emitted={} skipped={} intermediate_tuples={} seeks={} comparisons={} nexts={} opens={}",
                outcome.stats.emitted_rows,
                outcome.stats.skipped_rows,
                outcome.stats.intermediate_tuples_materialized,
                outcome.stats.trie_seeks,
                outcome.stats.trie_seek_comparisons,
                outcome.stats.trie_nexts,
                outcome.stats.trie_child_opens,
            );
            if let Some(naive) = &outcome.naive {
                println!(
                    "naive oracle: matches={} probes={} intermediate_tuples={} emitted={}",
                    naive.matches,
                    naive.naive_row_probes,
                    naive.naive_intermediate_tuples_materialized,
                    naive.naive_emitted_rows
                );
            }
            println!(
                "recorded: decision={:?} error_code={:?}",
                record.decision, record.error_code
            );
        }
        ValidateDecision::Rejected(record) => {
            println!("REJECTED: {record:?}");
        }
    }
    println!();
}

fn int_spec(name: &str, cols: &[&str], rows: Vec<[i64; 2]>) -> serde_json::Value {
    serde_json::json!({
        "name": name,
        "columns": cols.iter().map(|c| serde_json::json!({"name": c, "type": "int"})).collect::<Vec<_>>(),
        "rows": rows.into_iter().map(|r| vec![serde_json::Value::from(r[0]), serde_json::Value::from(r[1])]).collect::<Vec<_>>(),
    })
}

fn complete_graph(n: i64) -> Vec<[i64; 2]> {
    let mut edges = Vec::new();
    for i in 0..n {
        for j in (i + 1)..n {
            edges.push([i, j]);
        }
    }
    edges
}

fn triangle_request() -> serde_json::Value {
    let edges = complete_graph(5);
    serde_json::json!({
        "relations": [
            int_spec("R", &["a", "b"], edges.clone()),
            int_spec("S", &["b", "c"], edges.clone()),
            int_spec("T", &["a", "c"], edges),
        ],
        "compare_naive": true
    })
}

fn skew_request() -> serde_json::Value {
    let mut r: Vec<[i64; 2]> = (1..=200).map(|a| [a, 1]).collect();
    for b in 2..=101 {
        r.push([b + 1000, b]);
    }
    let mut s: Vec<[i64; 2]> = (1..=200).map(|c| [1, c]).collect();
    for b in 2..=101 {
        s.push([b, b + 9000]);
    }
    let t = vec![[7, 7], [42, 42], [100, 100], [200, 200], [1002, 9002]];

    serde_json::json!({
        "relations": [
            int_spec("R", &["a", "b"], r),
            int_spec("S", &["b", "c"], s),
            int_spec("T", &["a", "c"], t),
        ],
        "compare_naive": true
    })
}

fn rejected_request() -> serde_json::Value {
    serde_json::json!({
        "relations": [
            { "name": "r", "columns": [{"name": "a", "type": "int"}], "rows": [[null]] },
            { "name": "s", "columns": [{"name": "a", "type": "int"}], "rows": [[1]] },
        ]
    })
}

fn main() {
    tracing_subscriber::fmt().with_test_writer().try_init().ok();
    let state = Arc::new(AppState::new(Config::default()));

    run_case(
        "triangle over K5 (expect 10 rows)",
        triangle_request(),
        &state,
    );
    run_case(
        "skew: 40,000 hub prefixes, only 5 survive",
        skew_request(),
        &state,
    );

    println!("=== rejection category demonstration ===");
    match execute(
        &Config::default(),
        &state,
        parse_value(rejected_request()).unwrap(),
    ) {
        ValidateDecision::Rejected(record) => {
            println!(
                "decision={:?} code={:?} reasons={:?}",
                record.decision, record.error_code, record.reasons
            );
        }
        other => panic!("expected rejection, got a non-rejection decision: {other:?}"),
    }
}
