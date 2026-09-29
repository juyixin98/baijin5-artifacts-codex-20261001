//! Typed columnar batches backed by Arrow2 arrays.
//!
//! A [`TypedBatch`] owns one Arrow2 `Chunk` plus a parallel schema describing
//! the supported key type of each column. Row identity is the *physical row
//! index*: duplicate value rows keep distinct indices, which is what makes
//! output multiplicities correct.

use std::sync::Arc;

use arrow2::array::{Array, PrimitiveArray, Utf8Array};
use arrow2::chunk::Chunk;
use arrow2::datatypes::{DataType, Field, Schema};

use crate::error::{ErrorCode, JoinError, JoinResult};
use crate::types::value::{KeyType, Scalar};

/// One column: a name, a declared key type, and an Arrow2 array.
#[derive(Debug, Clone)]
pub struct Column {
    name: String,
    key_type: KeyType,
    array: Arc<dyn Array>,
}

impl Column {
    pub fn new(
        name: impl Into<String>,
        key_type: KeyType,
        array: Arc<dyn Array>,
    ) -> JoinResult<Self> {
        let col = Column {
            name: name.into(),
            key_type,
            array,
        };
        col.validate()?;
        Ok(col)
    }

    fn validate(&self) -> JoinResult<()> {
        let d = self.array.data_type();
        let ok = match self.key_type {
            KeyType::Int64 => d == &DataType::Int64,
            KeyType::Float64 => d == &DataType::Float64,
            KeyType::Utf8 => d == &DataType::Utf8,
        };
        if !ok {
            return Err(JoinError::input(
                ErrorCode::TypeMismatch,
                format!(
                    "column '{}' declared {} but array is {:?}",
                    self.name,
                    self.key_type.as_str(),
                    d
                ),
            ));
        }
        Ok(())
    }

    pub fn name(&self) -> &str {
        &self.name
    }
    pub fn key_type(&self) -> KeyType {
        self.key_type
    }
    pub fn array(&self) -> &Arc<dyn Array> {
        &self.array
    }
    pub fn len(&self) -> usize {
        self.array.len()
    }
    pub fn is_empty(&self) -> bool {
        self.array.len() == 0
    }

    /// Read one row as a [`Scalar`].
    pub fn get(&self, row: usize) -> Scalar {
        if row >= self.array.len() {
            return Scalar::Null;
        }
        match self.key_type {
            KeyType::Int64 => self
                .array
                .as_any()
                .downcast_ref::<PrimitiveArray<i64>>()
                .filter(|a| a.get(row).is_some())
                .map(|a| Scalar::Int(a.value(row)))
                .unwrap_or(Scalar::Null),
            KeyType::Float64 => self
                .array
                .as_any()
                .downcast_ref::<PrimitiveArray<f64>>()
                .filter(|a| a.get(row).is_some())
                .map(|a| Scalar::from_f64(a.value(row)))
                .unwrap_or(Scalar::Null),
            KeyType::Utf8 => self
                .array
                .as_any()
                .downcast_ref::<Utf8Array<i32>>()
                .filter(|a| a.get(row).is_some())
                .map(|a| Scalar::from_str_value(a.value(row)))
                .unwrap_or(Scalar::Null),
        }
    }

    /// Materialize the whole column as scalars. The operator works on these
    /// directly; Arrow2 remains the canonical wire/storage representation.
    pub fn to_scalars(&self) -> Vec<Scalar> {
        (0..self.len()).map(|i| self.get(i)).collect()
    }
}

/// A typed columnar batch: the unit of input and output transport.
#[derive(Debug, Clone)]
pub struct TypedBatch {
    columns: Vec<Column>,
    /// Arrow2 chunk kept so the batch can cross the API boundary unchanged.
    chunk: Chunk<Box<dyn Array>>,
}

impl TypedBatch {
    /// Build from columns; every column must have the same length.
    pub fn try_new(columns: Vec<Column>) -> JoinResult<Self> {
        if columns.is_empty() {
            return Err(JoinError::input(
                ErrorCode::InvalidPlan,
                "batch must contain at least one column",
            ));
        }
        let len = columns[0].len();
        for c in &columns {
            if c.len() != len {
                return Err(JoinError::input(
                    ErrorCode::ColumnLengthMismatch,
                    format!(
                        "column '{}' has {} rows, expected {}",
                        c.name(),
                        c.len(),
                        len
                    ),
                ));
            }
        }
        let arrays: Vec<Box<dyn Array>> = columns
            .iter()
            .map(|c| c.array().as_ref().to_boxed())
            .collect();
        let chunk = Chunk::try_new(arrays).map_err(|e| {
            JoinError::input(ErrorCode::ColumnLengthMismatch, format!("arrow chunk: {e}"))
        })?;
        Ok(Self { columns, chunk })
    }

    pub fn row_count(&self) -> usize {
        self.columns[0].len()
    }
    pub fn column_count(&self) -> usize {
        self.columns.len()
    }
    pub fn columns(&self) -> &[Column] {
        &self.columns
    }
    pub fn column(&self, idx: usize) -> Option<&Column> {
        self.columns.get(idx)
    }
    pub fn find(&self, name: &str) -> Option<usize> {
        self.columns.iter().position(|c| c.name() == name)
    }
    pub fn chunk(&self) -> &Chunk<Box<dyn Array>> {
        &self.chunk
    }

    pub fn arrow_schema(&self) -> Schema {
        Schema::from(
            self.columns
                .iter()
                .map(|c| Field::new(c.name(), c.array().data_type().clone(), true))
                .collect::<Vec<_>>(),
        )
    }
}
