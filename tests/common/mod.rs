#![allow(dead_code)] // helpers are shared across test crates; not every crate uses all of them
//! Shared test helpers: contexts, collection, reference data, logging.
//!
//! Reference answers are computed here with std-library code over
//! freshly-generated fixture rows — never by the operators under test.

use std::sync::Arc;
use std::time::Duration;

use pullq::batch::TypedBatch;
use pullq::error::QueryError;
use pullq::exec::ExecutionContext;
use pullq::fixtures::TableIter;
use pullq::operator::Operator;
use pullq::resources::ResourceRegistry;

/// Test-log macro: every line carries the run id so a failing run can be
/// replayed from the logs. Arguments after the run id are `format!` style.
macro_rules! tlog {
    ($run:expr, $($arg:tt)*) => {
        eprintln!("[tlog][{}] {}", $run, format!($($arg)*))
    };
}

pub(crate) use tlog;

/// Number of open file descriptors of this process (Linux /proc).
pub fn fd_count() -> usize {
    std::fs::read_dir("/proc/self/fd")
        .expect("read /proc/self/fd")
        .count()
}

/// A fresh execution context with its own resource registry and spill dir.
pub fn make_ctx(
    memory_limit: usize,
    timeout: Option<Duration>,
) -> (Arc<ExecutionContext>, Arc<ResourceRegistry>, tempfile::TempDir) {
    let dir = tempfile::tempdir().expect("tempdir");
    let resources = ResourceRegistry::new(memory_limit, dir.path().join("spill"));
    let ctx = ExecutionContext::new(Arc::clone(&resources), timeout);
    (ctx, resources, dir)
}

/// Drain an operator into a Vec of batches.
pub async fn collect_all(op: &mut dyn Operator) -> Result<Vec<TypedBatch>, QueryError> {
    let mut out = Vec::new();
    while let Some(batch) = op.next_batch().await? {
        out.push(batch);
    }
    Ok(out)
}

/// Extract all Int64 columns of a table as row tuples, regenerated from the
/// fixture (the input data source — not the code under test).
pub fn fixture_rows(table: &str, batches: usize, batch_rows: usize, seed: u64) -> Vec<Vec<i64>> {
    let iter = TableIter::new(table, batches, batch_rows, seed).expect("known table");
    let mut rows = Vec::new();
    for batch in iter {
        let n_cols = batch.chunk().arrays().len();
        for row in 0..batch.num_rows() {
            let mut values = Vec::with_capacity(n_cols);
            for col in 0..n_cols {
                match batch.int64_column(col) {
                    Ok(array) => values.push(array.value(row)),
                    Err(_) => values.push(i64::MIN), // Utf8 column marker; see fixture_strings
                }
            }
            rows.push(values);
        }
    }
    rows
}

/// Extract a Utf8 column as strings (fixture tables have at most one).
pub fn fixture_strings(
    table: &str,
    batches: usize,
    batch_rows: usize,
    seed: u64,
    col: usize,
) -> Vec<String> {
    use arrow2::array::Utf8Array;
    let iter = TableIter::new(table, batches, batch_rows, seed).expect("known table");
    let mut out = Vec::new();
    for batch in iter {
        let array = batch.chunk().arrays()[col]
            .as_any()
            .downcast_ref::<Utf8Array<i32>>()
            .expect("utf8 column");
        for row in 0..batch.num_rows() {
            out.push(array.value(row).to_string());
        }
    }
    out
}

/// Extract all Int64 columns of an operator-produced batch set.
pub fn batch_rows_i64(batches: &[TypedBatch]) -> Vec<Vec<i64>> {
    let mut rows = Vec::new();
    for batch in batches {
        let n_cols = batch.chunk().arrays().len();
        for row in 0..batch.num_rows() {
            let mut values = Vec::with_capacity(n_cols);
            for col in 0..n_cols {
                values.push(
                    batch
                        .int64_column(col)
                        .map(|a| a.value(row))
                        .unwrap_or(i64::MIN),
                );
            }
            rows.push(values);
        }
    }
    rows
}

/// Assert a resource snapshot is fully reclaimed, with rationale in the
/// failure message.
pub fn assert_resources_released(
    run_id: &str,
    snapshot: &pullq::resources::ResourceSnapshot,
    context: &str,
) {
    assert_eq!(
        snapshot.memory_used_bytes, 0,
        "[{run_id}] {context}: memory must return to zero (leaked reservation)"
    );
    assert_eq!(
        snapshot.reservations_live, 0,
        "[{run_id}] {context}: no live reservations may remain"
    );
    assert_eq!(
        snapshot.spill_files_live, 0,
        "[{run_id}] {context}: spill files must be deleted (leaked file guard)"
    );
    assert_eq!(
        snapshot.tasks_live, 0,
        "[{run_id}] {context}: tracked tasks must all have finished"
    );
}
