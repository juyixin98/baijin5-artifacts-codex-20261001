//! Hash join correctness against an independent std-HashMap reference.

mod common;

use std::collections::HashMap;
use std::time::Duration;

use arrow2::array::Utf8Array;
use pullq::exec::RunId;
use pullq::operator::join::{HashJoinOperator, JoinConfig};
use pullq::operator::scan::{ScanConfig, ScanOperator};
use pullq::operator::Operator;

use common::{
    assert_resources_released, collect_all, fixture_rows, fixture_strings, make_ctx, tlog,
};

const USER_ROWS: usize = 256; // ids 0..256, one batch
const ORDER_BATCHES: usize = 4;
const ORDER_BATCH_ROWS: usize = 128; // 512 orders, user_id in 0..256
const SEED: u64 = 13;

fn scan(
    ctx: &std::sync::Arc<pullq::exec::ExecutionContext>,
    table: &str,
    batches: usize,
    batch_rows: usize,
) -> ScanOperator {
    ScanOperator::new(
        ScanConfig {
            table: table.into(),
            batches,
            batch_rows,
            seed: SEED,
            delay_per_batch: Duration::ZERO,
            fail_at_batch: None,
        },
        std::sync::Arc::clone(ctx),
    )
    .expect("scan")
}

#[tokio::test]
async fn inner_join_matches_std_hashmap_reference() {
    let run_id = RunId::new().to_string();
    let (ctx, resources, _dir) = make_ctx(1 << 20, None);

    // users(id, name) ⨝ orders(id, user_id, amount) on id = user_id.
    // Output: orders.* + users.name (build key column is dropped).
    let build = scan(&ctx, "users", 1, USER_ROWS);
    let probe = scan(&ctx, "orders", ORDER_BATCHES, ORDER_BATCH_ROWS);
    let out_schema = {
        let mut fields = probe.schema().fields.clone();
        fields.push(build.schema().fields[1].clone()); // users.name
        arrow2::datatypes::Schema::from(fields)
    };
    let mut join = HashJoinOperator::new(
        Box::new(build),
        Box::new(probe),
        JoinConfig {
            key_build_col: 0, // users.id
            key_probe_col: 1, // orders.user_id
            build_keep_cols: vec![1],
            out_batch_rows: 100, // small output batches to exercise chunking
        },
        out_schema,
        std::sync::Arc::clone(&ctx),
    );

    let batches = collect_all(&mut join).await.expect("join must succeed");
    join.close().await.expect("close");
    ctx.shutdown().await;

    // Reference: std HashMap join over regenerated fixture rows.
    let user_ids = fixture_rows("users", 1, USER_ROWS, SEED);
    let user_names = fixture_strings("users", 1, USER_ROWS, SEED, 1);
    let mut names_by_id: HashMap<i64, String> = HashMap::new();
    for (row, name) in user_ids.iter().zip(user_names.iter()) {
        names_by_id.insert(row[0], name.clone());
    }
    let orders = fixture_rows("orders", ORDER_BATCHES, ORDER_BATCH_ROWS, SEED);
    let mut reference: Vec<(i64, i64, i64, String)> = Vec::new();
    for order in &orders {
        if let Some(name) = names_by_id.get(&order[1]) {
            reference.push((order[0], order[1], order[2], name.clone()));
        }
    }
    reference.sort();

    // Actual output rows.
    let mut actual: Vec<(i64, i64, i64, String)> = Vec::new();
    for batch in &batches {
        let id = batch.int64_column(0).expect("id");
        let user_id = batch.int64_column(1).expect("user_id");
        let amount = batch.int64_column(2).expect("amount");
        let name = batch.chunk().arrays()[3]
            .as_any()
            .downcast_ref::<Utf8Array<i32>>()
            .expect("name col");
        for row in 0..batch.num_rows() {
            actual.push((id.value(row), user_id.value(row), amount.value(row), name.value(row).to_string()));
        }
    }
    actual.sort();

    tlog!(run_id, "join produced {} rows; reference {} rows (orders={}, users={})", actual.len(), reference.len(), orders.len(), user_ids.len());
    assert_eq!(actual.len(), ORDER_BATCHES * ORDER_BATCH_ROWS, "[{run_id}] every order matches exactly one user (ids 0..256)");
    assert_eq!(actual, reference, "[{run_id}] join output must equal std-HashMap reference");
    // Spot-check concrete content, not just shape.
    let sample = &actual[0];
    assert_eq!(sample.3, format!("user-{}", sample.1), "[{run_id}] joined name must correspond to the order's user_id");

    assert_resources_released(&run_id, &resources.snapshot(), "join teardown");
    tlog!(run_id, "resources reclaimed after join");
}

#[tokio::test]
async fn join_with_empty_build_side_yields_no_rows() {
    let run_id = RunId::new().to_string();
    let (ctx, resources, _dir) = make_ctx(1 << 20, None);
    let build = scan(&ctx, "users", 0, USER_ROWS); // empty build side
    let probe = scan(&ctx, "orders", 1, 64);
    let out_schema = {
        let mut fields = probe.schema().fields.clone();
        fields.push(build.schema().fields[1].clone());
        arrow2::datatypes::Schema::from(fields)
    };
    let mut join = HashJoinOperator::new(
        Box::new(build),
        Box::new(probe),
        JoinConfig { key_build_col: 0, key_probe_col: 1, build_keep_cols: vec![1], out_batch_rows: 64 },
        out_schema,
        std::sync::Arc::clone(&ctx),
    );
    let batches = collect_all(&mut join).await.expect("empty build join ok");
    let rows: usize = batches.iter().map(|b| b.num_rows()).sum();
    tlog!(run_id, "empty build side -> {rows} output rows");
    assert_eq!(rows, 0, "[{run_id}] inner join with empty build side emits nothing");
    join.close().await.expect("close");
    ctx.shutdown().await;
    assert_resources_released(&run_id, &resources.snapshot(), "empty build teardown");
}
