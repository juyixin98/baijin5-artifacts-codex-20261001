//! Error propagation and stream poisoning: once any operator fails, the
//! failure crosses operator boundaries unchanged in category, and no
//! downstream stream can be consumed further.

mod common;

use std::time::Duration;

use pullq::error::ErrorCategory;
use pullq::exec::RunId;
use pullq::operator::join::{HashJoinOperator, JoinConfig};
use pullq::operator::scan::{ScanConfig, ScanOperator};
use pullq::operator::sort::{SortConfig, SortOperator};
use pullq::operator::{Operator, OperatorState};

use common::{assert_resources_released, make_ctx, tlog};

fn scan(ctx: &std::sync::Arc<pullq::exec::ExecutionContext>, table: &str, fail_at: Option<usize>) -> ScanOperator {
    ScanOperator::new(
        ScanConfig {
            table: table.into(),
            batches: 6,
            batch_rows: 32,
            seed: 11,
            delay_per_batch: Duration::ZERO,
            fail_at_batch: fail_at,
        },
        std::sync::Arc::clone(ctx),
    )
    .expect("scan")
}

#[tokio::test]
async fn scan_failure_poisons_its_own_stream() {
    let run_id = RunId::new().to_string();
    let (ctx, resources, _dir) = make_ctx(1 << 20, None);
    let mut scan = scan(&ctx, "numbers", Some(1));

    let first = scan.next_batch().await.expect("batch 0 ok").expect("some");
    assert_eq!(first.num_rows(), 32, "[{run_id}] batch before failure is intact");
    let err = scan.next_batch().await.expect_err("injected failure at batch 1");
    tlog!(run_id, "injected failure: category={:?} msg={err}", err.category());
    assert_eq!(err.category(), ErrorCategory::Compute, "[{run_id}] injected failure is a Compute error");
    assert_eq!(scan.state(), OperatorState::Errored, "[{run_id}] state after error");

    for attempt in 0..3 {
        let err = scan.next_batch().await.expect_err("poisoned stream must stay poisoned");
        assert_eq!(
            err.category(),
            ErrorCategory::StateConflict,
            "[{run_id}] attempt {attempt}: poll after error must be StateConflict, never more data"
        );
    }
    scan.close().await.expect("close after error");
    scan.close().await.expect("idempotent close");
    ctx.shutdown().await;
    assert_resources_released(&run_id, &resources.snapshot(), "scan failure teardown");
}

#[tokio::test]
async fn scan_failure_propagates_through_sort_and_poisons_it() {
    let run_id = RunId::new().to_string();
    let (ctx, resources, _dir) = make_ctx(1 << 20, None);
    let failing = scan(&ctx, "numbers", Some(2));
    let mut sort = SortOperator::new(
        Box::new(failing),
        SortConfig { key_col: 0, run_rows: 64, max_spill_bytes: 1 << 20 },
        std::sync::Arc::clone(&ctx),
    );

    let err = sort.next_batch().await.expect_err("sort must surface the scan failure");
    tlog!(run_id, "sort surfaced: category={:?} msg={err}", err.category());
    assert_eq!(err.category(), ErrorCategory::Compute, "[{run_id}] category preserved across operator boundary");
    let again = sort.next_batch().await.expect_err("sort poisoned");
    assert_eq!(again.category(), ErrorCategory::StateConflict, "[{run_id}] sort stream poisoned too");
    sort.close().await.expect("close");
    ctx.shutdown().await;
    assert_resources_released(&run_id, &resources.snapshot(), "sort propagation teardown");
}

#[tokio::test]
async fn build_side_failure_aborts_join_before_any_output() {
    let run_id = RunId::new().to_string();
    let (ctx, resources, _dir) = make_ctx(1 << 20, None);
    let build = scan(&ctx, "users", Some(0)); // fails on first build batch
    let probe = scan(&ctx, "orders", None);
    let out_schema = probe.schema().clone();
    let mut join = HashJoinOperator::new(
        Box::new(build),
        Box::new(probe),
        JoinConfig {
            key_build_col: 0,
            key_probe_col: 1,
            build_keep_cols: vec![], // no payload columns needed for this test
            out_batch_rows: 64,
        },
        out_schema,
        std::sync::Arc::clone(&ctx),
    );

    let err = join.next_batch().await.expect_err("join must fail during build phase");
    tlog!(run_id, "join build-phase failure: category={:?} msg={err}", err.category());
    assert_eq!(err.category(), ErrorCategory::Compute, "[{run_id}] build failure category preserved");
    let again = join.next_batch().await.expect_err("join poisoned");
    assert_eq!(again.category(), ErrorCategory::StateConflict, "[{run_id}] join stream poisoned");
    join.close().await.expect("close");
    ctx.shutdown().await;
    assert_resources_released(&run_id, &resources.snapshot(), "join failure teardown");
}

#[tokio::test]
async fn join_fails_with_resource_exhausted_when_build_side_exceeds_budget() {
    let run_id = RunId::new().to_string();
    // Budget far below one users batch (32 rows * ~24B + map overhead).
    let (ctx, resources, _dir) = make_ctx(64, None);
    let build = scan(&ctx, "users", None);
    let probe = scan(&ctx, "orders", None);
    let out_schema = probe.schema().clone();
    let mut join = HashJoinOperator::new(
        Box::new(build),
        Box::new(probe),
        JoinConfig { key_build_col: 0, key_probe_col: 1, build_keep_cols: vec![], out_batch_rows: 64 },
        out_schema,
        std::sync::Arc::clone(&ctx),
    );
    let err = join.next_batch().await.expect_err("build side cannot fit the budget");
    tlog!(run_id, "join budget failure: category={:?} msg={err}", err.category());
    assert_eq!(
        err.category(),
        ErrorCategory::ResourceExhausted,
        "[{run_id}] oversized build side must be ResourceExhausted (join does not spill)"
    );
    join.close().await.expect("close");
    ctx.shutdown().await;
    assert_resources_released(&run_id, &resources.snapshot(), "join budget teardown");
}
