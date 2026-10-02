//! Typed record batches — the data contract between operators.
//!
//! A [`TypedBatch`] owns its column data (`Box<dyn Array>`) and holds the
//! schema by `Arc`. It deliberately holds **no** reference to the execution
//! context, resource registry, or any operator: once a batch is returned to
//! the consumer its lifetime is fully independent, and it stays readable
//! after the whole execution tree is torn down.

use std::sync::Arc;

use arrow2::array::{Array, PrimitiveArray, Utf8Array};
use arrow2::chunk::Chunk;
use arrow2::datatypes::{DataType, Schema};

use crate::error::QueryError;

#[derive(Clone)]
pub struct TypedBatch {
    schema: Arc<Schema>,
    chunk: Chunk<Box<dyn Array>>,
}

impl std::fmt::Debug for TypedBatch {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let names: Vec<&str> = self.schema.fields.iter().map(|f| f.name.as_str()).collect();
        f.debug_struct("TypedBatch")
            .field("columns", &names)
            .field("rows", &self.num_rows())
            .finish()
    }
}

impl TypedBatch {
    /// Construct a batch, validating that the chunk matches the schema
    /// (column count, types, equal lengths). Violations are `Input` errors:
    /// they indicate a malformed producer, caught at the module boundary.
    pub fn new(schema: Arc<Schema>, chunk: Chunk<Box<dyn Array>>) -> Result<Self, QueryError> {
        if chunk.arrays().len() != schema.fields.len() {
            return Err(QueryError::input(format!(
                "batch has {} columns but schema has {} fields",
                chunk.arrays().len(),
                schema.fields.len()
            )));
        }
        for (array, field) in chunk.arrays().iter().zip(schema.fields.iter()) {
            if array.data_type() != &field.data_type {
                return Err(QueryError::input(format!(
                    "column '{}' has type {:?}, expected {:?}",
                    field.name,
                    array.data_type(),
                    field.data_type
                )));
            }
        }
        Ok(Self { schema, chunk })
    }

    pub fn schema(&self) -> &Arc<Schema> {
        &self.schema
    }

    pub fn chunk(&self) -> &Chunk<Box<dyn Array>> {
        &self.chunk
    }

    pub fn num_rows(&self) -> usize {
        self.chunk.len()
    }

    pub fn is_empty(&self) -> bool {
        self.num_rows() == 0
    }

    /// Downcast column `col` to `Int64`. A mismatch is a `Compute` error: the
    /// schema was validated at plan time, so this means an invariant broke.
    pub fn int64_column(&self, col: usize) -> Result<&PrimitiveArray<i64>, QueryError> {
        let array = self
            .chunk
            .arrays()
            .get(col)
            .ok_or_else(|| QueryError::compute(format!("column index {col} out of range")))?;
        array
            .as_any()
            .downcast_ref::<PrimitiveArray<i64>>()
            .ok_or_else(|| {
                QueryError::compute(format!("column {col} is not Int64: {:?}", array.data_type()))
            })
    }

    /// Rough heap footprint, used for memory-budget accounting. Only the
    /// types admitted by plan validation are sized precisely.
    pub fn estimated_bytes(&self) -> usize {
        self.chunk.arrays().iter().map(|a| estimate_array_bytes(a.as_ref())).sum()
    }
}

pub fn estimate_array_bytes(array: &dyn Array) -> usize {
    let len = array.len();
    let validity_bytes = array.validity().map_or(0, |b| b.len().div_ceil(8));
    let data_bytes = match array.data_type() {
        DataType::Int64 => len * 8,
        DataType::Utf8 => {
            let values = array
                .as_any()
                .downcast_ref::<Utf8Array<i32>>()
                .map(|a| a.values().len() + (len + 1) * 4)
                .unwrap_or(0);
            values
        }
        // Unsupported types are rejected at plan validation; fall back to a
        // conservative per-row estimate so accounting never underflows to 0.
        _ => len * 16,
    };
    data_bytes + validity_bytes
}
