//! Shared test helpers. Expected results are always produced by the
//! independent `pull_query::oracle`, never by the operators under test.
#![allow(dead_code)]

use pull_query::batch::{Batch, Scalar, Schema};
use pull_query::error::QueryResult;
use pull_query::operators::batch_rows;
use std::sync::Arc;

pub fn rows(batches: &[Batch]) -> Vec<Vec<Scalar>> {
    batches
        .iter()
        .flat_map(|b| batch_rows(b).expect("rows"))
        .collect()
}

pub fn ints_at(rows: &[Vec<Scalar>], col: usize) -> Vec<Option<i64>> {
    rows.iter()
        .map(|r| match &r[col] {
            Scalar::Int(v) => *v,
            other => panic!("expected int at col {col}, got {other:?}"),
        })
        .collect()
}

pub fn schema(cols: &[(&str, pull_query::ColumnType)]) -> Arc<Schema> {
    Arc::new(Schema::new(
        cols.iter().map(|(n, t)| (n.to_string(), *t)).collect(),
    ))
}

pub fn batch(sch: Arc<Schema>, rs: &[Vec<Scalar>]) -> Batch {
    use pull_query::batch::BatchBuilder;
    let mut b = BatchBuilder::with_capacity(sch, rs.len());
    for r in rs {
        b.add_row(r).expect("valid row");
    }
    b.finish().expect("batch")
}

pub fn i(v: i64) -> Scalar {
    Scalar::Int(Some(v))
}
#[allow(dead_code)]
pub fn inull() -> Scalar {
    Scalar::Int(None)
}
#[allow(dead_code)]
pub fn s(v: &str) -> Scalar {
    Scalar::Utf8(Some(v.to_string()))
}

/// Run `f` against a fresh temp spill root, cleaning up afterwards.
pub fn with_spill_root(f: impl FnOnce(std::path::PathBuf)) {
    let root = std::env::temp_dir().join(format!(
        "pq-test-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    std::fs::create_dir_all(&root).unwrap();
    f(root.clone());
    let _ = std::fs::remove_dir_all(&root);
}

#[allow(dead_code)]
pub fn ok_unwrap<T>(r: QueryResult<T>) -> T {
    r.expect("ok")
}
