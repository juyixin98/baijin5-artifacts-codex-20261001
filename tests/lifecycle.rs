//! Batch lifecycle: a returned batch is independent of the execution
//! context — it stays readable after the operator, the context, and every
//! resource guard are gone.

mod common;

use std::time::Duration;

use pullq::exec::RunId;
use pullq::operator::scan::{ScanConfig, ScanOperator};
use pullq::operator::Operator;

use common::{fixture_rows, make_ctx, tlog};

#[tokio::test]
async fn returned_batch_outlives_execution_context() {
    let run_id = RunId::new().to_string();
    let (ctx, resources, _dir) = make_ctx(1 << 20, None);

    let batch = {
        let mut scan = ScanOperator::new(
            ScanConfig {
                table: "numbers".into(),
                batches: 4,
                batch_rows: 64,
                seed: 21,
                delay_per_batch: Duration::ZERO,
                fail_at_batch: None,
            },
            std::sync::Arc::clone(&ctx),
        )
        .expect("scan");
        let batch = scan.next_batch().await.expect("batch").expect("non-empty");
        scan.close().await.expect("close");
        batch // moved out; operator dropped here
    };
    ctx.shutdown().await;
    drop(ctx); // execution context fully gone
    let snapshot = resources.snapshot();
    assert_eq!(snapshot.memory_used_bytes, 0, "[{run_id}] context teardown complete");

    // The batch must still be fully readable and equal to the reference row
    // range (batches are sequential: batch 0 holds ordinals 0..64 in `v`).
    assert_eq!(batch.num_rows(), 64, "[{run_id}] row count intact after teardown");
    let reference = fixture_rows("numbers", 4, 64, 21);
    let keys = batch.int64_column(0).expect("key col");
    let vals = batch.int64_column(1).expect("val col");
    for (row, expected) in reference.iter().enumerate().take(64) {
        assert_eq!(vals.value(row), expected[1], "[{run_id}] row {row} value readable after teardown");
        assert_eq!(keys.value(row), expected[0], "[{run_id}] row {row} key readable after teardown");
    }
    tlog!(run_id, "batch of {} rows verified against reference after context dropped", batch.num_rows());
}
