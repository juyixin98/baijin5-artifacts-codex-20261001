//! Blocking sort: correctness against the independent oracle, disk spill
//! behavior, and full resource reclamation after close.

mod common;

use std::sync::Arc;
use std::time::Instant;

use pull_query::cancel::{CancellationToken, Control};
use pull_query::diag::RunDiag;
use pull_query::fixture;
use pull_query::operator::{run_to_completion, Operator};
use pull_query::operators::{batch_rows, Scan, Sort};
use pull_query::oracle;
use pull_query::resource::ResourceTracker;

use common::ints_at;

fn temp_root() -> std::path::PathBuf {
    std::env::temp_dir().join(format!(
        "pq-sort-test-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ))
}

struct Built {
    sort: Sort,
    ctrl: Control,
    token: CancellationToken,
    tracker: Arc<ResourceTracker>,
    root: std::path::PathBuf,
    expected: Vec<Vec<pull_query::Scalar>>,
}

fn build_spill_sort(rows_n: usize, batch_size: usize, budget: u64) -> Built {
    let schema = fixture::orders_schema();
    let raw = fixture::orders_rows(rows_n);
    let batches = fixture::batches(schema.clone(), &raw, batch_size).unwrap();
    let tracker = ResourceTracker::new();
    let root = temp_root();

    // Independent oracle computes the expected order on `amount` (col 2).
    let expected = oracle::sort_rows(&raw, &[2]);

    let sort = Sort::new(
        "sort",
        Box::new(Scan::new("scan", schema, batches)),
        &["amount".to_string()],
        budget,
        tracker.clone(),
        root.clone(),
    )
    .unwrap()
    .with_out_batch_rows(7);

    let token = CancellationToken::new();
    let ctrl = Control::new(token.clone(), None, RunDiag::new());
    Built {
        sort,
        ctrl,
        token,
        tracker,
        root,
        expected,
    }
}

#[test]
fn in_memory_sort_matches_oracle() {
    let mut b = build_spill_sort(20, 5, u64::MAX);
    assert_eq!(b.sort.runs_spilled(), 0);
    let (out, err) = run_to_completion(&mut b.sort, &b.ctrl);
    assert!(err.is_none(), "unexpected error: {err:?}");

    let mut got = Vec::new();
    for batch in &out {
        got.extend(batch_rows(batch).unwrap());
    }
    assert_eq!(got.len(), b.expected.len());
    assert_eq!(ints_at(&got, 2), ints_at(&b.expected, 2));
    // Full row equality, not just the key column.
    assert_eq!(got, b.expected);
    assert_eq!(b.tracker.open_files(), 0);
    assert_eq!(b.tracker.buffered_bytes(), 0);
}

#[test]
fn spill_sort_produces_multiple_runs_and_matches_oracle() {
    // Tiny budget forces several spills across 80 rows in batches of 6.
    let mut b = build_spill_sort(80, 6, 120);
    let runs_before = b.sort.runs_spilled();
    let (out, err) = run_to_completion(&mut b.sort, &b.ctrl);
    assert!(err.is_none(), "unexpected error: {err:?}");

    // Multiple runs must actually have hit disk.
    let runs = b.tracker.spill_files_created();
    assert!(runs >= 3, "expected >=3 spilled runs, got {runs}");
    let _ = runs_before;

    let mut got = Vec::new();
    for batch in &out {
        got.extend(batch_rows(batch).unwrap());
    }
    assert_eq!(got.len(), 80);
    assert_eq!(got, b.expected, "spill-merge order differs from oracle");
    assert!(b.tracker.spilled_bytes() > 0);

    // Everything reclaimed after close.
    assert_eq!(b.tracker.open_files(), 0, "file handles leaked");
    assert_eq!(b.tracker.buffered_bytes(), 0, "buffered bytes leaked");
    assert!(
        !b.root.exists() || std::fs::read_dir(&b.root).map(|d| d.count()).unwrap_or(0) == 0,
        "spill directory not cleaned: {:?}",
        b.root
    );
    let _ = b.token;
}

#[test]
fn output_batches_are_bounded_in_size() {
    let mut b = build_spill_sort(40, 50, 90).with_out_batch(5);
    let (out, err) = run_to_completion(&mut b.sort, &b.ctrl);
    assert!(err.is_none(), "unexpected error: {err:?}");
    assert!(out.len() > 1, "expected multiple output batches");
    for batch in &out {
        assert!(
            batch.num_rows() <= 5,
            "batch had {} rows, cap is 5",
            batch.num_rows()
        );
    }
}

#[test]
fn returned_batches_stay_readable_after_cancel() {
    let mut b = build_spill_sort(30, 5, 60);
    let mut collected = Vec::new();
    // Pull one output batch, then cancel before draining the rest.
    let first = b.sort.next(&b.ctrl).unwrap().unwrap();
    collected.push(first);
    b.token.cancel();
    let err = b.sort.next(&b.ctrl).unwrap_err();
    assert_eq!(err.kind(), pull_query::ErrorKind::Cancelled);

    // The already-returned batch is still fully readable post-cancel.
    let readable = batch_rows(&collected[0]).unwrap();
    assert!(!readable.is_empty());
    assert_eq!(readable[0].len(), 4);

    b.sort.shutdown(Some(&b.ctrl));
    assert_eq!(b.tracker.open_files(), 0);
}

#[test]
fn double_close_is_idempotent_and_releases_once() {
    let mut b = build_spill_sort(10, 4, 40);
    // Drain fully so the merge phase opens readers.
    let _ = run_to_completion(&mut b.sort, &b.ctrl);
    b.sort.shutdown(Some(&b.ctrl));
    b.sort.shutdown(Some(&b.ctrl));
    assert_eq!(b.tracker.open_files(), 0);
    // next() after close is a state conflict, never a panic or re-entry.
    let err = b.sort.next(&b.ctrl).unwrap_err();
    assert_eq!(err.kind(), pull_query::ErrorKind::StateConflict);
    let _ = Instant::now();
}

// Small extension to keep the bounded-size test compiling without clutter.
impl Built {
    fn with_out_batch(mut self, n: usize) -> Self {
        self.sort.set_out_batch_rows(n);
        self
    }
}
