//! Validation entry: malformed plans and malformed batches are rejected as
//! `Input` errors before any execution happens.

mod common;

use std::sync::Arc;

use arrow2::array::Int64Array;
use arrow2::chunk::Chunk;
use arrow2::datatypes::{DataType, Field, Schema};
use pullq::batch::TypedBatch;
use pullq::error::ErrorCategory;
use pullq::exec::RunId;
use pullq::plan::{build_plan, Plan};

use common::{make_ctx, tlog};

fn scan_plan(table: &str) -> Plan {
    Plan::Scan {
        table: table.into(),
        batches: None,
        batch_rows: None,
        seed: None,
        delay_ms_per_batch: None,
        fail_at_batch: None,
    }
}

#[tokio::test]
async fn unknown_table_is_an_input_error() {
    let run_id = RunId::new().to_string();
    let (ctx, _r, _d) = make_ctx(1 << 20, None);
    let err = build_plan(&scan_plan("does_not_exist"), &ctx).err().expect("unknown table");
    tlog!(run_id, "unknown table: category={:?} msg={err}", err.category());
    assert_eq!(err.category(), ErrorCategory::Input, "[{run_id}] unknown table is rejected at validation");
}

#[tokio::test]
async fn sort_on_missing_or_wrongly_typed_key_is_an_input_error() {
    let run_id = RunId::new().to_string();
    let (ctx, _r, _d) = make_ctx(1 << 20, None);

    let missing = Plan::Sort {
        key: "nope".into(),
        input: Box::new(scan_plan("numbers")),
        run_rows: None,
        max_spill_bytes: None,
    };
    let err = build_plan(&missing, &ctx).err().expect("missing key");
    tlog!(run_id, "missing sort key: category={:?} msg={err}", err.category());
    assert_eq!(err.category(), ErrorCategory::Input, "[{run_id}] missing key column");

    let wrong_type = Plan::Sort {
        key: "name".into(), // Utf8, not Int64
        input: Box::new(scan_plan("users")),
        run_rows: None,
        max_spill_bytes: None,
    };
    let err = build_plan(&wrong_type, &ctx).err().expect("wrong key type");
    tlog!(run_id, "wrong-typed sort key: category={:?} msg={err}", err.category());
    assert_eq!(err.category(), ErrorCategory::Input, "[{run_id}] non-Int64 key column");
}

#[tokio::test]
async fn join_output_name_collision_is_an_input_error() {
    let run_id = RunId::new().to_string();
    let (ctx, _r, _d) = make_ctx(1 << 20, None);
    // users ⨝ users: build keeps `name`, which collides with probe's `name`.
    let plan = Plan::Join {
        build_key: "id".into(),
        probe_key: "id".into(),
        build: Box::new(scan_plan("users")),
        probe: Box::new(scan_plan("users")),
    };
    let err = build_plan(&plan, &ctx).err().expect("name collision");
    tlog!(run_id, "join collision: category={:?} msg={err}", err.category());
    assert_eq!(err.category(), ErrorCategory::Input, "[{run_id}] output name collision");
}

#[tokio::test]
async fn valid_join_plan_builds_and_reports_output_schema() {
    let run_id = RunId::new().to_string();
    let (ctx, _r, _d) = make_ctx(1 << 20, None);
    let plan = Plan::Join {
        build_key: "id".into(),
        probe_key: "user_id".into(),
        build: Box::new(scan_plan("users")),
        probe: Box::new(scan_plan("orders")),
    };
    let built = build_plan(&plan, &ctx).expect("valid join plan");
    let names: Vec<&str> = built.schema.fields.iter().map(|f| f.name.as_str()).collect();
    tlog!(run_id, "join output schema: {names:?}");
    assert_eq!(names, ["id", "user_id", "amount", "name"], "[{run_id}] probe columns + build payload, build key dropped");
}

#[test]
fn malformed_batch_is_an_input_error() {
    let run_id = RunId::new().to_string();
    let schema = Arc::new(Schema::from(vec![
        Field::new("a", DataType::Int64, false),
        Field::new("b", DataType::Int64, false),
    ]));
    // Only one column for a two-column schema.
    let chunk = Chunk::new(vec![Int64Array::from_vec(vec![1, 2, 3]).boxed()]);
    let err = TypedBatch::new(Arc::clone(&schema), chunk).expect_err("column count mismatch");
    tlog!(run_id, "batch column mismatch: category={:?} msg={err}", err.category());
    assert_eq!(err.category(), ErrorCategory::Input, "[{run_id}] malformed batch rejected at the data boundary");

    // Wrong column type.
    let chunk = Chunk::new(vec![
        Int64Array::from_vec(vec![1]).boxed(),
        arrow2::array::Utf8Array::<i32>::from_slice(["x"]).boxed(),
    ]);
    let err = TypedBatch::new(schema, chunk).expect_err("type mismatch");
    assert_eq!(err.category(), ErrorCategory::Input, "[{run_id}] type mismatch rejected");
}
