//! End-to-end execution through `exec::execute`: plan → tree → stats, with
//! expected ordering again taken from the independent oracle.

mod common;

use pull_query::cancel::CancellationToken;
use pull_query::error::ErrorKind;
use pull_query::exec::{self, QueryRequest, Source};
use pull_query::fixture;
use pull_query::operators::batch_rows;
use pull_query::oracle;
use pull_query::validate::Scenario;
use std::time::Duration;

#[test]
fn flat_scan_returns_all_rows() {
    let req = QueryRequest {
        source: Source::Users,
        batch_size: 3,
        ..Default::default()
    };
    let out = exec::execute(&req, CancellationToken::new()).unwrap();
    assert!(out.error.is_none());
    let mut got = Vec::new();
    for b in &out.batches {
        got.extend(batch_rows(b).unwrap());
    }
    assert_eq!(got, fixture::users_rows());
    assert_eq!(out.stats.rows_out, 8);
    assert_eq!(out.stats.open_files_after_close, 0);
}

#[test]
fn spilled_sort_and_project_match_oracle() {
    let req = QueryRequest {
        source: Source::Orders { rows: 50 },
        batch_size: 6,
        sort_keys: vec!["amount".to_string()],
        sort_memory_budget_bytes: Some(120),
        project: vec!["amount".to_string(), "region".to_string()],
        limit: None,
        timeout_ms: None,
    };
    let out = exec::execute(&req, CancellationToken::new()).unwrap();
    assert!(out.error.is_none(), "{:?}", out.error);
    assert!(out.stats.runs_spilled >= 3, "spill did not occur");
    assert_eq!(out.stats.open_files_after_close, 0);
    assert_eq!(out.stats.buffered_bytes_after_close, 0);

    // Projected output schema.
    assert_eq!(
        out.batches[0]
            .schema()
            .fields()
            .iter()
            .map(|(n, _)| n.as_str())
            .collect::<Vec<_>>(),
        vec!["amount", "region"]
    );

    let mut got = Vec::new();
    for b in &out.batches {
        got.extend(batch_rows(b).unwrap());
    }
    let raw = fixture::orders_rows(50);
    let sorted = oracle::sort_rows(&raw, &[2]);
    let expected = oracle::project_rows(&sorted, &[2, 3]);
    assert_eq!(got, expected);
}

#[test]
fn limit_truncates_results() {
    let req = QueryRequest {
        source: Source::Orders { rows: 30 },
        batch_size: 4,
        limit: Some(5),
        ..Default::default()
    };
    let out = exec::execute(&req, CancellationToken::new()).unwrap();
    assert_eq!(out.stats.rows_out, 5);
}

#[test]
fn unknown_sort_key_is_invalid_input() {
    let req = QueryRequest {
        source: Source::Users,
        sort_keys: vec!["nope".to_string()],
        ..Default::default()
    };
    let err = exec::execute(&req, CancellationToken::new()).err().unwrap();
    assert_eq!(err.kind(), ErrorKind::InvalidInput);
}

#[test]
fn unknown_projection_column_is_invalid_input() {
    let req = QueryRequest {
        source: Source::Users,
        project: vec!["ghost".to_string()],
        ..Default::default()
    };
    let err = exec::execute(&req, CancellationToken::new()).err().unwrap();
    assert_eq!(err.kind(), ErrorKind::InvalidInput);
}

#[test]
fn timeout_request_reports_timeout_category() {
    // Use the blocking scenario so the deadline reliably elapses inside a
    // cancel-aware sleep (a plain in-memory scan is far too fast to time out).
    let out = Scenario::Timeout {
        per_batch_delay: Duration::from_millis(100),
        deadline: Duration::from_millis(15),
    }
    .run();
    let err = out.error.expect("expected timeout");
    assert_eq!(err.kind(), ErrorKind::Timeout);
    assert_eq!(out.tracker.open_files(), 0);
}
