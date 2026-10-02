//! Sort operator: external spill correctness and resource reclamation.
//!
//! Reference answers are produced by sorting fixture rows with `Vec::sort`
//! in this test — never by the sort operator itself.

mod common;

use std::time::Duration;

use pullq::error::ErrorCategory;
use pullq::exec::RunId;
use pullq::operator::scan::{ScanConfig, ScanOperator};
use pullq::operator::sort::{SortConfig, SortOperator};
use pullq::operator::Operator;

use common::{
    assert_resources_released, batch_rows_i64, collect_all, fixture_rows, make_ctx, tlog,
};

const BATCHES: usize = 8;
const BATCH_ROWS: usize = 128;
const RUN_ROWS: usize = 128;
const SEED: u64 = 7;
/// One numbers run is 128 rows * 16 B = 2048 B; 3072 B fits exactly one run.
const MEM_LIMIT: usize = 3072;

fn sort_over_numbers(ctx: &std::sync::Arc<pullq::exec::ExecutionContext>) -> SortOperator {
    let scan = ScanOperator::new(
        ScanConfig {
            table: "numbers".into(),
            batches: BATCHES,
            batch_rows: BATCH_ROWS,
            seed: SEED,
            delay_per_batch: Duration::ZERO,
            fail_at_batch: None,
        },
        std::sync::Arc::clone(ctx),
    )
    .expect("scan config valid");
    SortOperator::new(
        Box::new(scan),
        SortConfig { key_col: 0, run_rows: RUN_ROWS, max_spill_bytes: 1 << 20 },
        std::sync::Arc::clone(ctx),
    )
}

#[tokio::test]
async fn sort_spills_to_disk_and_matches_reference() {
    let run_id = RunId::new().to_string();
    let (ctx, resources, _dir) = make_ctx(MEM_LIMIT, Some(Duration::from_secs(30)));
    tlog!(run_id, "setup: {BATCHES} batches x {BATCH_ROWS} rows, run_rows={RUN_ROWS}, mem_limit={MEM_LIMIT}B");

    let mut sort = sort_over_numbers(&ctx);
    let batches = collect_all(&mut sort).await.expect("sort must succeed");
    tlog!(
        run_id,
        "after run: runs_in_memory={} runs_spilled={} (expect 1 in memory, 7 spilled: budget fits one 2048B run)",
        sort.runs_in_memory,
        sort.runs_spilled
    );
    assert_eq!(sort.runs_in_memory, 1, "[{run_id}] exactly one run fits the budget");
    assert_eq!(sort.runs_spilled, BATCHES - 1, "[{run_id}] remaining runs must spill");

    // Reference: same fixture rows, sorted with std sort (independent impl).
    let mut reference = fixture_rows("numbers", BATCHES, BATCH_ROWS, SEED);
    reference.sort();
    let mut actual = batch_rows_i64(&batches);
    // Sort order on keys is what the operator guarantees; compare as sorted
    // multisets AND verify the emitted key sequence is non-decreasing.
    let keys: Vec<i64> = actual.iter().map(|r| r[0]).collect();
    assert!(
        keys.windows(2).all(|w| w[0] <= w[1]),
        "[{run_id}] emitted keys must be non-decreasing (merge invariant)"
    );
    actual.sort();
    assert_eq!(
        actual.len(),
        BATCHES * BATCH_ROWS,
        "[{run_id}] row count must be preserved"
    );
    assert_eq!(actual, reference, "[{run_id}] sorted output must equal std-sorted reference");
    tlog!(run_id, "correctness: {} rows, keys non-decreasing, multiset equals reference", actual.len());

    // Resource reclamation while the operator is still open (runs held).
    let mid = resources.snapshot();
    tlog!(run_id, "mid-state: memory_used={}B spill_live={} spill_created={}", mid.memory_used_bytes, mid.spill_files_live, mid.spill_files_created_total);
    assert_eq!(mid.spill_files_created_total, BATCHES - 1, "[{run_id}] spill counter must record every spilled run");

    sort.close().await.expect("close");
    sort.close().await.expect("second close is idempotent");
    ctx.shutdown().await;
    let end = resources.snapshot();
    assert_resources_released(&run_id, &end, "after sort close");
    tlog!(run_id, "after close: snapshot={end:?}");
    let leftovers = std::fs::read_dir(resources.spill_dir()).map(|d| d.count()).unwrap_or(0);
    assert_eq!(leftovers, 0, "[{run_id}] spill directory must be empty after close");
}

#[tokio::test]
async fn sort_fails_with_resource_exhausted_when_memory_and_spill_quota_insufficient() {
    let run_id = RunId::new().to_string();
    // Memory fits nothing (tiny) and spill quota fits nothing either.
    let (ctx, resources, _dir) = make_ctx(128, None);
    let scan = ScanOperator::new(
        ScanConfig {
            table: "numbers".into(),
            batches: 2,
            batch_rows: 128,
            seed: 1,
            delay_per_batch: Duration::ZERO,
            fail_at_batch: None,
        },
        std::sync::Arc::clone(&ctx),
    )
    .expect("scan");
    let mut sort = SortOperator::new(
        Box::new(scan),
        SortConfig { key_col: 0, run_rows: 128, max_spill_bytes: 128 },
        std::sync::Arc::clone(&ctx),
    );
    let err = sort.next_batch().await.expect_err("must fail: no memory, no spill quota");
    tlog!(run_id, "failure category={:?} msg={err}", err.category());
    assert_eq!(
        err.category(),
        ErrorCategory::ResourceExhausted,
        "[{run_id}] budget+quota exhaustion must surface as ResourceExhausted, not a generic error"
    );
    // Poisoned stream: further polls are state conflicts, not more compute.
    let again = sort.next_batch().await.expect_err("poisoned stream");
    assert_eq!(again.category(), ErrorCategory::StateConflict, "[{run_id}] poll after error");
    sort.close().await.expect("close after error");
    assert_resources_released(&run_id, &resources.snapshot(), "after resource-exhaustion close");
}

#[tokio::test]
async fn sort_empty_input_yields_empty_output() {
    let run_id = RunId::new().to_string();
    let (ctx, resources, _dir) = make_ctx(1 << 20, None);
    let scan = ScanOperator::new(
        ScanConfig {
            table: "numbers".into(),
            batches: 0,
            batch_rows: 16,
            seed: 1,
            delay_per_batch: Duration::ZERO,
            fail_at_batch: None,
        },
        std::sync::Arc::clone(&ctx),
    )
    .expect("scan");
    let mut sort = SortOperator::new(
        Box::new(scan),
        SortConfig { key_col: 0, run_rows: 16, max_spill_bytes: 1 << 20 },
        std::sync::Arc::clone(&ctx),
    );
    assert!(sort.next_batch().await.expect("empty sort ok").is_none(), "[{run_id}] empty input -> immediate end-of-stream");
    sort.close().await.expect("close");
    assert_resources_released(&run_id, &resources.snapshot(), "empty sort");
}
