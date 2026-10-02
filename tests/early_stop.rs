//! Downstream early stop: a consumer that takes a few batches and then
//! closes (or just drops) the tree must leave zero resources behind.
//!
//! File-handle assertions live in `resource_reclamation.rs` (fd counts are
//! process-global, so those tests run serialized in their own binary).

mod common;

use std::time::Duration;

use pullq::error::ErrorCategory;
use pullq::exec::RunId;
use pullq::operator::scan::{ScanConfig, ScanOperator};
use pullq::operator::sort::{SortConfig, SortOperator};
use pullq::operator::Operator;

use common::{assert_resources_released, make_ctx, tlog};

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

#[tokio::test]
async fn early_close_reclaims_everything_and_blocks_further_polls() {
    let run_id = RunId::new().to_string();
    // Small budget so several runs spill: close must delete spill files too.
    let (ctx, resources, _dir) = make_ctx(1024, None);
    let scan = numbers_scan(&ctx, 10);
    let mut sort = SortOperator::new(
        Box::new(scan),
        SortConfig { key_col: 0, run_rows: 128, max_spill_bytes: 1 << 20 },
        std::sync::Arc::clone(&ctx),
    );

    let first = sort.next_batch().await.expect("first sorted batch").expect("non-empty");
    tlog!(run_id, "consumer took 1 batch of {} rows out of 2560, then stops", first.num_rows());
    let mid = resources.snapshot();
    tlog!(run_id, "at early stop: memory_used={}B spill_live={} spill_created={}", mid.memory_used_bytes, mid.spill_files_live, mid.spill_files_created_total);
    assert!(mid.spill_files_created_total > 0, "[{run_id}] test requires spilled runs to prove they are reclaimed");

    sort.close().await.expect("early close");
    let err = sort.next_batch().await.expect_err("poll after close");
    assert_eq!(err.category(), ErrorCategory::StateConflict, "[{run_id}] poll after close is a state conflict");
    sort.close().await.expect("second close idempotent");
    ctx.shutdown().await;

    let end = resources.snapshot();
    assert_resources_released(&run_id, &end, "after early close");
    // The batch returned before the stop is still fully readable.
    assert!(first.num_rows() > 0, "[{run_id}] returned batch survives teardown");
    let _ = first.int64_column(0).expect("key column readable after close");
}

#[tokio::test]
async fn dropping_the_tree_without_close_still_reclaims_resources() {
    let run_id = RunId::new().to_string();
    let (ctx, resources, _dir) = make_ctx(1024, None);
    {
        let scan = numbers_scan(&ctx, 10);
        let mut sort = SortOperator::new(
            Box::new(scan),
            SortConfig { key_col: 0, run_rows: 128, max_spill_bytes: 1 << 20 },
            std::sync::Arc::clone(&ctx),
        );
        let _first = sort.next_batch().await.expect("one batch").expect("non-empty");
        tlog!(run_id, "dropping operator tree without close (consumer vanished)");
        // No close(): guards must release on drop.
    }
    ctx.shutdown().await;
    let end = resources.snapshot();
    tlog!(run_id, "after drop: snapshot={end:?}");
    assert_resources_released(&run_id, &end, "drop-based reclamation");
}
