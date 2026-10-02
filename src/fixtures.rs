//! Deterministic synthetic tables — all data in this project is local and
//! generated, no external systems involved.
//!
//! Generators are seeded (SplitMix64) so tests can regenerate the exact same
//! input and compute reference answers with independent std-library code
//! (the reference is never produced by the operators under test).
//!
//! Tables:
//! - `numbers(k Int64, v Int64)`  — pseudo-random keys, for sort tests
//! - `users(id Int64, name Utf8)` — sequential ids, build side of joins
//! - `orders(id Int64, user_id Int64, amount Int64)` — probe side of joins

use std::sync::Arc;

use arrow2::array::{Int64Array, Utf8Array};
use arrow2::chunk::Chunk;
use arrow2::datatypes::{DataType, Field, Schema};

use crate::batch::TypedBatch;
use crate::error::QueryError;

pub const TABLE_NUMBERS: &str = "numbers";
pub const TABLE_USERS: &str = "users";
pub const TABLE_ORDERS: &str = "orders";

/// Modulus for `orders.user_id`: orders reference users in `0..USER_MOD`.
pub const USER_MOD: i64 = 256;

/// Deterministic 64-bit PRNG (SplitMix64). Small, seedable, reproducible.
pub struct SplitMix64(u64);

impl SplitMix64 {
    pub fn new(seed: u64) -> Self {
        Self(seed)
    }

    pub fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }
}

pub fn table_schema(table: &str) -> Result<Schema, QueryError> {
    let fields = match table {
        TABLE_NUMBERS => vec![
            Field::new("k", DataType::Int64, false),
            Field::new("v", DataType::Int64, false),
        ],
        TABLE_USERS => vec![
            Field::new("id", DataType::Int64, false),
            Field::new("name", DataType::Utf8, false),
        ],
        TABLE_ORDERS => vec![
            Field::new("id", DataType::Int64, false),
            Field::new("user_id", DataType::Int64, false),
            Field::new("amount", DataType::Int64, false),
        ],
        other => {
            return Err(QueryError::input(format!(
                "unknown table '{other}'; available: {TABLE_NUMBERS}, {TABLE_USERS}, {TABLE_ORDERS}"
            )))
        }
    };
    Ok(Schema::from(fields))
}

/// Lazily generates batches for a table. Deterministic for a given seed.
pub struct TableIter {
    table: String,
    schema: Arc<Schema>,
    batches_left: usize,
    batch_rows: usize,
    rng: SplitMix64,
    ordinal: i64,
}

impl TableIter {
    pub fn new(
        table: &str,
        batches: usize,
        batch_rows: usize,
        seed: u64,
    ) -> Result<Self, QueryError> {
        Ok(Self {
            table: table.to_string(),
            schema: Arc::new(table_schema(table)?),
            batches_left: batches,
            batch_rows,
            rng: SplitMix64::new(seed),
            ordinal: 0,
        })
    }

    pub fn schema(&self) -> &Arc<Schema> {
        &self.schema
    }

    fn next_numbers(&mut self) -> TypedBatch {
        let mut keys = Vec::with_capacity(self.batch_rows);
        let mut vals = Vec::with_capacity(self.batch_rows);
        for _ in 0..self.batch_rows {
            keys.push((self.rng.next_u64() % 1_000_000) as i64);
            vals.push(self.ordinal);
            self.ordinal += 1;
        }
        let chunk = Chunk::new(vec![
            Int64Array::from_vec(keys).boxed(),
            Int64Array::from_vec(vals).boxed(),
        ]);
        TypedBatch::new(Arc::clone(&self.schema), chunk).expect("fixture batch matches schema")
    }

    fn next_users(&mut self) -> TypedBatch {
        let mut ids = Vec::with_capacity(self.batch_rows);
        let mut names = Vec::with_capacity(self.batch_rows);
        for _ in 0..self.batch_rows {
            ids.push(self.ordinal);
            names.push(format!("user-{}", self.ordinal));
            self.ordinal += 1;
        }
        let chunk = Chunk::new(vec![
            Int64Array::from_vec(ids).boxed(),
            Utf8Array::<i32>::from_iter_values(names.iter()).boxed(),
        ]);
        TypedBatch::new(Arc::clone(&self.schema), chunk).expect("fixture batch matches schema")
    }

    fn next_orders(&mut self) -> TypedBatch {
        let mut ids = Vec::with_capacity(self.batch_rows);
        let mut user_ids = Vec::with_capacity(self.batch_rows);
        let mut amounts = Vec::with_capacity(self.batch_rows);
        for _ in 0..self.batch_rows {
            ids.push(self.ordinal);
            user_ids.push((self.rng.next_u64() % USER_MOD as u64) as i64);
            amounts.push((self.rng.next_u64() % 10_000) as i64);
            self.ordinal += 1;
        }
        let chunk = Chunk::new(vec![
            Int64Array::from_vec(ids).boxed(),
            Int64Array::from_vec(user_ids).boxed(),
            Int64Array::from_vec(amounts).boxed(),
        ]);
        TypedBatch::new(Arc::clone(&self.schema), chunk).expect("fixture batch matches schema")
    }
}

impl Iterator for TableIter {
    type Item = TypedBatch;

    fn next(&mut self) -> Option<Self::Item> {
        if self.batches_left == 0 {
            return None;
        }
        self.batches_left -= 1;
        Some(match self.table.as_str() {
            TABLE_NUMBERS => self.next_numbers(),
            TABLE_USERS => self.next_users(),
            TABLE_ORDERS => self.next_orders(),
            other => unreachable!("unknown table validated at construction: {other}"),
        })
    }
}
