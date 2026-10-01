//! Typed batches: logical schema, Arrow2 chunks, canonical row keys.

pub mod csv;
pub mod decode;
pub mod encode;
pub mod json;
pub mod schema;

pub use csv::{TypedCsvReader, read_typed_csv, read_typed_csv_batches};
pub use decode::decode_keys;
pub use encode::{RowKey, canonical_hash, encode_rows, level_partition, splitmix64};
pub use json::{batch_to_json, build_batch};
pub use schema::{LogicalType, Schema, SchemaField};

use arrow2::chunk::Chunk;

/// A typed batch: a horizontal slice of rows following [`Schema`], backed by
/// Arrow2 arrays. All batches flowing through an operator share one schema;
/// left/right schema equality is validated before execution.
#[derive(Debug, Clone)]
pub struct TypedBatch {
    pub schema: Schema,
    pub chunk: Chunk<Box<dyn arrow2::array::Array>>,
}

impl TypedBatch {
    pub fn new(schema: Schema, chunk: Chunk<Box<dyn arrow2::array::Array>>) -> Self {
        Self { schema, chunk }
    }

    pub fn rows(&self) -> usize {
        self.chunk.len()
    }

    pub fn columns(&self) -> usize {
        self.schema.len()
    }
}
