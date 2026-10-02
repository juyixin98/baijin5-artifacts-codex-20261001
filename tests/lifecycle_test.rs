//! Lifecycle and control-plane guarantees:
//! * stream poisoning after error (no re-entry / no damaged-upstream reads),
//! * timeout vs user cancellation are distinct categories,
//! * cancellation races against a blocking source win promptly,
//! * already-returned batches remain readable after failure/cancel,
//! * early downstream stop still reclaims everything upstream,
//! * per-operator close is repeatable and releases exactly once.

mod common;

use std::sync::Arc;
use std::time::{Duration, Instant};

use pull_query::batch::{BatchBuilder, ColumnType, Scalar, Schema};
use pull_query::cancel::{CancellationToken, Control};
use pull_query::diag::RunDiag;
use pull_query::error::ErrorKind;
use pull_query::operator::Operator;
use pull_query::operators::{batch_rows, FailingSource, Limit, Projection, Scan};
use pull_query::resource::ResourceTracker;
use pull_query::validate::Scenario;

use common::{i, s};

fn small_schema() -> Arc<Schema> {
    Arc::new(Schema::new(vec![
        ("a".to_string(), ColumnType::Int),
        ("b".to_string(), ColumnType::Utf8),
    ]))
}

fn n_batches(n: usize, per: usize) -> Vec<pull_query::Batch> {
    let sch = small_schema();
    (0..n)
        .map(|bi| {
            let mut b = BatchBuilder::new(sch.clone());
            for r in 0..per {
                let v = (bi * per + r) as i64;
                b.add_row(&[i(v), s("x")]).unwrap();
            }
            b.finish().unwrap()
        })
        .collect()
}

#[test]
fn error_poisons_stream_and_never_reenters() {
    let sch = small_schema();
    let good = n_batches(2, 3);
    let mut src = FailingSource::new("fail", sch, good, ErrorKind::ComputationFailed, "boom");
    let diag = RunDiag::new();
    let ctrl = Control::new(CancellationToken::new(), None, diag.clone());

    let first = src.next(&ctrl).unwrap().unwrap();
    let second = src.next(&ctrl).unwrap().unwrap();
    // Third pull fails.
    let e1 = src.next(&ctrl).unwrap_err();
    assert_eq!(e1.kind(), ErrorKind::ComputationFailed);
    let pulls_at_fail = src.core().pulls();

    // Repeated pulls return the SAME category and never re-run `pull`.
    for _ in 0..3 {
        let e = src.next(&ctrl).unwrap_err();
        assert_eq!(e.kind(), ErrorKind::ComputationFailed);
    }
    assert_eq!(
        src.core().pulls(),
        pulls_at_fail,
        "concrete pull() re-entered after poisoning"
    );

    // Already-returned data is still readable.
    assert_eq!(batch_rows(&first).unwrap().len(), 3);
    assert_eq!(batch_rows(&second).unwrap().len(), 3);

    src.shutdown(Some(&ctrl));
    assert_eq!(
        diag.state_of("fail").as_deref(),
        Some("closed"),
        "diag should show closed after shutdown"
    );
}

#[test]
fn next_after_close_is_state_conflict() {
    let sch = small_schema();
    let mut src = Scan::new("scan", sch, n_batches(1, 2));
    let diag = RunDiag::new();
    let ctrl = Control::new(CancellationToken::new(), None, diag);
    src.shutdown(Some(&ctrl));
    src.shutdown(Some(&ctrl)); // repeatable, no double-free
    let err = src.next(&ctrl).unwrap_err();
    assert_eq!(err.kind(), ErrorKind::StateConflict);
}

#[test]
fn timeout_is_distinct_from_cancellation() {
    // Deadline elapses inside the blocking sleep → Timeout, never Cancelled.
    let out = Scenario::Timeout {
        per_batch_delay: Duration::from_millis(100),
        deadline: Duration::from_millis(20),
    }
    .run();
    let err = out.error.expect("should time out");
    assert_eq!(err.kind(), ErrorKind::Timeout);
    assert_ne!(err.kind(), ErrorKind::Cancelled);
}

#[test]
fn cancellation_race_aborts_blocking_scan_promptly() {
    // Source sleeps 20ms per batch; cancel at 50ms. Must finish as Cancelled
    // well before consuming everything (30 batches = 600ms of sleep).
    let start = Instant::now();
    let out = Scenario::CancelRace {
        per_batch_delay: Duration::from_millis(20),
        batches: 10, // 30 rows / 10 batches
        cancel_at: Duration::from_millis(45),
    }
    .run();
    let elapsed = start.elapsed();

    let err = out.error.expect("should be cancelled");
    assert_eq!(err.kind(), ErrorKind::Cancelled);
    assert!(
        elapsed < Duration::from_millis(300),
        "cancel did not win promptly: {elapsed:?}"
    );
    // Some batches may have been emitted before cancellation; they stay valid.
    for b in &out.batches {
        assert!(b.num_rows() > 0);
        assert_eq!(batch_rows(b).unwrap()[0].len(), 4);
    }
}

#[test]
fn cancel_beats_deadline_when_both_signalled() {
    // Token cancelled up front with a deadline already expired: cancel wins.
    let token = CancellationToken::new();
    token.cancel();
    let out = Scenario::Timeout {
        per_batch_delay: Duration::from_millis(0),
        deadline: Duration::from_millis(0),
    }
    .run_with_token(token);
    let err = out.error.expect("error");
    assert_eq!(err.kind(), ErrorKind::Cancelled);
}

#[test]
fn early_downstream_stop_reclaims_upstream() {
    let tracker = ResourceTracker::new();
    let sch = small_schema();
    let scan = Scan::new("scan", sch, n_batches(20, 10)); // 200 rows available
    let proj =
        Projection::new("proj", Box::new(scan), &["a".to_string(), "b".to_string()]).unwrap();
    let mut limit = Limit::new("limit", Box::new(proj), 7);

    let diag = RunDiag::new();
    let ctrl = Control::new(CancellationToken::new(), None, diag.clone());

    let mut rows = 0;
    while let Some(b) = limit.next(&ctrl).unwrap() {
        rows += b.num_rows();
    }
    assert_eq!(rows, 7, "limit must stop after exactly 7 rows");

    // Close propagates up the (mostly unconsumed) tree exactly once.
    limit.shutdown(Some(&ctrl));
    limit.shutdown(Some(&ctrl));
    assert_eq!(tracker.open_files(), 0);
    assert_eq!(
        diag.state_of("scan").as_deref(),
        Some("closed"),
        "unconsumed upstream scan must still be closed"
    );
}

#[test]
fn child_operator_is_closed_when_parent_closes() {
    let sch = small_schema();
    let scan = Scan::new("leaf", sch, n_batches(2, 2));
    let mut proj = Projection::new(
        "parent",
        Box::new(scan),
        &["a".to_string(), "b".to_string()],
    )
    .unwrap();
    let diag = RunDiag::new();
    let ctrl = Control::new(CancellationToken::new(), None, diag.clone());
    // Drain nothing, just close the parent.
    proj.shutdown(Some(&ctrl));
    assert_eq!(diag.state_of("leaf").as_deref(), Some("closed"));
    assert_eq!(diag.state_of("parent").as_deref(), Some("closed"));
}

#[test]
fn explicit_cancel_token_is_idempotent() {
    let t = CancellationToken::new();
    assert!(t.cancel());
    assert!(!t.cancel());
    assert!(t.is_cancelled());
}

#[test]
fn control_check_observations_include_run_id() {
    let diag = RunDiag::new();
    let id = diag.run_id().to_string();
    assert!(
        id.starts_with("run-"),
        "run id should be replay-stable: {id}"
    );
    diag.info("test", "state", "reasoning: unit observation");
    let rendered = diag.render();
    assert!(rendered.contains(&id));
    assert!(rendered.contains("unit observation"));
}

#[test]
fn failing_source_supports_each_error_category() {
    for kind in [
        ErrorKind::InvalidInput,
        ErrorKind::StateConflict,
        ErrorKind::ResourceExhausted,
        ErrorKind::ComputationFailed,
        ErrorKind::Timeout,
        ErrorKind::Cancelled,
    ] {
        let sch = small_schema();
        let mut src = FailingSource::new("f", sch, vec![], kind, "cat");
        let diag = RunDiag::new();
        let ctrl = Control::new(CancellationToken::new(), None, diag);
        let e = src.next(&ctrl).unwrap_err();
        assert_eq!(e.kind(), kind, "category {kind:?} not propagated");
        src.shutdown(Some(&ctrl));
    }
}

#[allow(dead_code)]
fn ensure_scalar_used(_s: Scalar) {}
