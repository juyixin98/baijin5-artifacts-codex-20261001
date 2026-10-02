//! Blocking input: a scan that blocks before each batch must still observe
//! cancellation, and a timeout must be distinguishable from a user cancel.

mod common;

use std::time::{Duration, Instant};

use pullq::error::{CancelKind, ErrorCategory};
use pullq::exec::RunId;
use pullq::operator::scan::{ScanConfig, ScanOperator};
use pullq::operator::Operator;

use common::{assert_resources_released, make_ctx, tlog};

fn slow_scan(ctx: &std::sync::Arc<pullq::exec::ExecutionContext>, batches: usize) -> ScanOperator {
    ScanOperator::new(
        ScanConfig {
            table: "numbers".into(),
            batches,
            batch_rows: 16,
            seed: 3,
            delay_per_batch: Duration::from_millis(100),
            fail_at_batch: None,
        },
        std::sync::Arc::clone(ctx),
    )
    .expect("scan")
}

#[tokio::test]
async fn blocked_pull_is_interrupted_by_deadline_with_timeout_category() {
    let run_id = RunId::new().to_string();
    let (ctx, resources, _dir) = make_ctx(1 << 20, Some(Duration::from_millis(150)));
    let mut scan = slow_scan(&ctx, 50);
    let started = Instant::now();

    // First batch arrives after one 100ms delay, inside the deadline.
    let first = scan.next_batch().await.expect("first batch within deadline").expect("non-empty");
    assert_eq!(first.num_rows(), 16, "[{run_id}] first batch intact");
    // Second pull blocks past the 150ms deadline.
    let err = scan.next_batch().await.expect_err("deadline must fire");
    let elapsed = started.elapsed();
    tlog!(run_id, "deadline fired after {elapsed:?}; category={:?}", err.category());
    assert!(
        elapsed >= Duration::from_millis(140),
        "[{run_id}] cancellation must not fire before the deadline ({elapsed:?})"
    );
    assert_eq!(
        err.category(),
        ErrorCategory::CancelledTimeout,
        "[{run_id}] deadline expiry must be CancelledTimeout, never confused with a user cancel"
    );
    // The stream is poisoned by the error.
    let again = scan.next_batch().await.expect_err("poisoned");
    assert_eq!(again.category(), ErrorCategory::StateConflict, "[{run_id}] poll after error");

    scan.close().await.expect("close");
    ctx.shutdown().await;
    assert_resources_released(&run_id, &resources.snapshot(), "timeout teardown");
}

#[tokio::test]
async fn blocked_pull_is_interrupted_by_user_cancel_with_user_category() {
    let run_id = RunId::new().to_string();
    // No deadline: only the explicit user cancel can stop this query.
    let (ctx, resources, _dir) = make_ctx(1 << 20, None);
    let mut scan = slow_scan(&ctx, 50);
    let token = ctx.cancel.clone();
    let canceller = tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(50)).await;
        token.cancel(CancelKind::User);
    });

    let err = scan.next_batch().await.expect_err("user cancel must interrupt the blocked pull");
    tlog!(run_id, "user cancel observed; category={:?}", err.category());
    assert_eq!(
        err.category(),
        ErrorCategory::CancelledUser,
        "[{run_id}] explicit cancel must be CancelledUser (distinct from timeout)"
    );
    canceller.await.expect("canceller joins");
    scan.close().await.expect("close");
    ctx.shutdown().await;
    assert_resources_released(&run_id, &resources.snapshot(), "user-cancel teardown");
}

#[tokio::test]
async fn cancellation_propagates_through_sort_blocking_accumulation() {
    let run_id = RunId::new().to_string();
    let (ctx, resources, _dir) = make_ctx(1 << 20, None);
    // Sort over a slow scan: the cancel must cross the sort boundary and
    // abort the scan it is blocked on.
    let scan = slow_scan(&ctx, 50);
    let mut sort = pullq::operator::sort::SortOperator::new(
        Box::new(scan),
        pullq::operator::sort::SortConfig { key_col: 0, run_rows: 64, max_spill_bytes: 1 << 20 },
        std::sync::Arc::clone(&ctx),
    );
    let token = ctx.cancel.clone();
    let canceller = tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(250)).await;
        token.cancel(CancelKind::User);
    });
    let started = Instant::now();
    let err = sort.next_batch().await.expect_err("cancel must propagate scan -> sort");
    tlog!(run_id, "cancel crossed sort boundary after {:?}; category={:?}", started.elapsed(), err.category());
    assert_eq!(err.category(), ErrorCategory::CancelledUser, "[{run_id}] propagated cancel keeps its kind");
    sort.close().await.expect("close");
    canceller.await.expect("join");
    ctx.shutdown().await;
    assert_resources_released(&run_id, &resources.snapshot(), "propagated-cancel teardown");
}
