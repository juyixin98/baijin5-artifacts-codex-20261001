//! Local synthetic fixtures. No external accounts or real business data: every
//! dataset is generated deterministically in-process so tests and demos are
//! reproducible and offline.

use std::sync::Arc;

use crate::batch::{Batch, BatchBuilder, ColumnType, Scalar, Schema};
use crate::error::QueryResult;

/// A small `users` table: (id, name, active).
pub fn users_schema() -> Arc<Schema> {
    Arc::new(Schema::new(vec![
        ("id".to_string(), ColumnType::Int),
        ("name".to_string(), ColumnType::Utf8),
        ("active".to_string(), ColumnType::Bool),
    ]))
}

/// A small `orders` table: (order_id, user_id, amount, region).
pub fn orders_schema() -> Arc<Schema> {
    Arc::new(Schema::new(vec![
        ("order_id".to_string(), ColumnType::Int),
        ("user_id".to_string(), ColumnType::Int),
        ("amount".to_string(), ColumnType::Int),
        ("region".to_string(), ColumnType::Utf8),
    ]))
}

/// The fixed users rows (including a null `name` and an inactive user to
/// exercise null-key join exclusion and sorting nulls-first).
pub fn users_rows() -> Vec<Vec<Scalar>> {
    vec![
        row_iub(1, "ada", true),
        row_iub(2, "ben", false),
        row_iub(3, "cleo", true),
        row_iub(4, "dina", true),
        row_iub(5, "??", true),
        vec![
            Scalar::Int(Some(6)),
            Scalar::Utf8(None),
            Scalar::Bool(Some(false)),
        ],
        row_iub(7, "gina", true),
        row_iub(8, "hugo", true),
    ]
}

fn row_iub(id: i64, name: &str, active: bool) -> Vec<Scalar> {
    vec![
        Scalar::Int(Some(id)),
        Scalar::Utf8(Some(name.to_string())),
        Scalar::Bool(Some(active)),
    ]
}

/// Deterministic orders: `n` rows with cycling user ids, amounts and regions.
/// `user_id` 6 collides with the null-name user to show join still keys on id;
/// some orders reference user id 9 (absent) to show unmatched probe rows drop.
pub fn orders_rows(n: usize) -> Vec<Vec<Scalar>> {
    let regions = ["north", "south", "east", "west"];
    let user_ids = [1, 2, 3, 4, 5, 6, 7, 8, 9];
    (0..n)
        .map(|i| {
            let uid = user_ids[i % user_ids.len()];
            // Non-monotonic amounts so sorting visibly reorders.
            let amount = ((i as i64) * 37 % 1000) - 200;
            let region = regions[i % regions.len()];
            vec![
                Scalar::Int(Some(1000 + i as i64)),
                Scalar::Int(Some(uid)),
                Scalar::Int(Some(amount)),
                Scalar::Utf8(Some(region.to_string())),
            ]
        })
        .collect()
}

pub fn users_batch() -> QueryResult<Batch> {
    build(users_schema(), &users_rows())
}

pub fn orders_batch(n: usize) -> QueryResult<Batch> {
    build(orders_schema(), &orders_rows(n))
}

/// Split rows into batches of at most `batch_size`.
pub fn chunk_rows(rows: &[Vec<Scalar>], batch_size: usize) -> Vec<std::ops::Range<usize>> {
    let mut out = Vec::new();
    let mut start = 0;
    while start < rows.len() {
        let end = (start + batch_size).min(rows.len());
        out.push(start..end);
        start = end;
    }
    out
}

pub fn build(schema: Arc<Schema>, rows: &[Vec<Scalar>]) -> QueryResult<Batch> {
    let mut b = BatchBuilder::with_capacity(schema, rows.len());
    for r in rows {
        b.add_row(r)?;
    }
    b.finish()
}

/// Build several batches of `rows` chunked to `batch_size`.
pub fn batches(
    schema: Arc<Schema>,
    rows: &[Vec<Scalar>],
    batch_size: usize,
) -> QueryResult<Vec<Batch>> {
    chunk_rows(rows, batch_size)
        .into_iter()
        .map(|r| build(schema.clone(), &rows[r]))
        .collect()
}
