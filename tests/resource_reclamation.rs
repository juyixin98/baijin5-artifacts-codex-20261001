// FD_LOCK guards are intentionally held across awaits to serialize fd-sensitive
// tests; each #[tokio::test] runs on its own current-thread runtime, so the
// std mutex cannot deadlock here.
#![allow(clippy::await_holding_lock)]

//! File-handle reclamation tests.
//!
//! `/proc/self/fd` is process-global, and `cargo test` runs tests of one
//! binary concurrently — a neighbouring test's runtime sockets would look
//! like "leaked" handles. All fd-asserting tests therefore live in this one
//! binary and serialize on `FD_LOCK`, so the process-wide fd count is only
//! affected by the test currently holding the lock.

mod common;

use std::sync::Mutex;
use std::time::Duration;

use pullq::exec::RunId;
use pullq::operator::scan::{ScanConfig, ScanOperator};
use pullq::operator::sort::{SortConfig, SortOperator};
use pullq::operator::Operator;

use common::{assert_resources_released, fd_count, make_ctx, tlog};

static FD_LOCK: Mutex<()> = Mutex::new(());

fn numbers_scan(ctx: &std::sync::Arc<pullq::exec::ExecutionContext>, batches: usize) -> ScanOperator {
    ScanOperator::new(
        ScanConfig {
            table: "numbers".into(),
            batches,
            batch_rows: 256,
            seed: 5,
            delay_per_batch: Duration::ZERO,
            fail_at_batch: None,
        },
        std::sync::Arc::clone(ctx),
    )
    .expect("scan")
}

fn tight_sort(input: ScanOperator, ctx: &std::sync::Arc<pullq::exec::ExecutionContext>) -> SortOperator {
    SortOperator::new(
        Box::new(input),
        SortConfig { key_col: 0, run_rows: 128, max_spill_bytes: 1 << 20 },
        std::sync::Arc::clone(ctx),
    )
}

#[tokio::test]
async fn fds_reclaimed_after_full_sort_with_spill() {
    let _guard = FD_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let run_id = RunId::new().to_string();
    let (ctx, resources, _dir) = make_ctx(1024, None);
    let fd_before = fd_count();

    let mut sort = tight_sort(numbers_scan(&ctx, 10), &ctx);
    let mut batches = 0usize;
    while sort.next_batch().await.expect("sort").is_some() {
        batches += 1;
    }
    let mid = resources.snapshot();
    let fd_mid = fd_count();
    tlog!(run_id, "full sort: {batches} output batches; mid fds={fd_mid} (baseline {fd_before}), spill_live={}", mid.spill_files_live);
    assert!(mid.spill_files_created_total > 0, "[{run_id}] test requires spilled runs");

    sort.close().await.expect("close");
    ctx.shutdown().await;
    let fd_after = fd_count();
    tlog!(run_id, "after close: fds={fd_after} (baseline {fd_before})");
    assert_eq!(fd_after, fd_before, "[{run_id}] every merge reader and spill handle must be closed");
    assert_resources_released(&run_id, &resources.snapshot(), "fd test: full sort");
}

#[tokio::test]
async fn fds_reclaimed_after_early_close() {
    let _guard = FD_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let run_id = RunId::new().to_string();
    let (ctx, resources, _dir) = make_ctx(1024, None);
    let fd_before = fd_count();

    let mut sort = tight_sort(numbers_scan(&ctx, 10), &ctx);
    let _first = sort.next_batch().await.expect("first batch").expect("non-empty");
    let fd_mid = fd_count();
    tlog!(run_id, "early stop after 1 batch: fds mid={fd_mid} (baseline {fd_before}, 10 merge readers open)");
    assert!(fd_mid > fd_before, "[{run_id}] merge readers must be open mid-run (sanity check)");

    sort.close().await.expect("early close");
    ctx.shutdown().await;
    let fd_after = fd_count();
    tlog!(run_id, "after early close: fds={fd_after} (baseline {fd_before})");
    assert_eq!(fd_after, fd_before, "[{run_id}] early close must close all merge readers");
    assert_resources_released(&run_id, &resources.snapshot(), "fd test: early close");
}

#[tokio::test]
async fn fds_reclaimed_when_tree_dropped_without_close() {
    let _guard = FD_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let run_id = RunId::new().to_string();
    let (ctx, resources, _dir) = make_ctx(1024, None);
    let fd_before = fd_count();
    {
        let mut sort = tight_sort(numbers_scan(&ctx, 10), &ctx);
        let _first = sort.next_batch().await.expect("first batch").expect("non-empty");
        tlog!(run_id, "dropping tree mid-merge without close");
    }
    ctx.shutdown().await;
    let fd_after = fd_count();
    tlog!(run_id, "after drop: fds={fd_after} (baseline {fd_before})");
    assert_eq!(fd_after, fd_before, "[{run_id}] drop alone must release every handle");
    assert_resources_released(&run_id, &resources.snapshot(), "fd test: drop");
}
