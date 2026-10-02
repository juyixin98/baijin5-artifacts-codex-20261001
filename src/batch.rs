//! Typed batches: the unit of data that moves between operators.
//!
//! A [`Batch`] pairs an Arrow2 [`Chunk`] with a small, strongly-typed
//! [`Schema`]. Batches are *owned* and independent of the execution
//! [`crate::cancel::Control`]: once an operator hands a batch to its consumer,
//! the consumer may read it for as long as it likes even after the producing
//! operator errors, is cancelled, or closes. This is the concrete expression of
//! "returned-batch lifetime is separate from execution context".
//!
//! Only three logical types are modeled (64-bit integer, UTF-8 string, boolean)
//! plus nulls. That is enough to exercise sorting, spilling and joins while
//! keeping the data/error contract explicit.

use std::sync::Arc;

use arrow2::array::{
    Array, BooleanArray, MutableArray, MutableBooleanArray, MutablePrimitiveArray,
    MutableUtf8Array, PrimitiveArray, Utf8Array,
};
use arrow2::chunk::Chunk;
use arrow2::datatypes::{DataType, Field};

use crate::error::{QueryError, QueryResult};

/// Logical, type-checked column type supported by the framework.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ColumnType {
    /// Signed 64-bit integer.
    Int,
    /// UTF-8 variable-length string.
    Utf8,
    /// Boolean.
    Bool,
}

impl ColumnType {
    pub fn arrow(self) -> DataType {
        match self {
            ColumnType::Int => DataType::Int64,
            ColumnType::Utf8 => DataType::Utf8,
            ColumnType::Bool => DataType::Boolean,
        }
    }

    fn from_arrow(dt: &DataType) -> QueryResult<Self> {
        match dt {
            DataType::Int64 => Ok(ColumnType::Int),
            DataType::Utf8 => Ok(ColumnType::Utf8),
            DataType::Boolean => Ok(ColumnType::Bool),
            other => Err(QueryError::invalid_input(format!(
                "unsupported column type: {other:?}"
            ))),
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            ColumnType::Int => "int",
            ColumnType::Utf8 => "utf8",
            ColumnType::Bool => "bool",
        }
    }
}

/// An ordered, named set of columns. Shared via `Arc` so a batch and its
/// projections can reference one schema without copying names.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Schema {
    fields: Vec<(String, ColumnType)>,
}

impl Schema {
    pub fn new(fields: Vec<(String, ColumnType)>) -> Self {
        Self { fields }
    }

    pub fn fields(&self) -> &[(String, ColumnType)] {
        &self.fields
    }

    pub fn len(&self) -> usize {
        self.fields.len()
    }

    pub fn is_empty(&self) -> bool {
        self.fields.is_empty()
    }

    pub fn index_of(&self, name: &str) -> Option<usize> {
        self.fields.iter().position(|(n, _)| n == name)
    }

    pub fn arrow_fields(&self) -> Vec<Field> {
        self.fields
            .iter()
            .map(|(n, t)| Field::new(n.clone(), t.arrow(), true))
            .collect()
    }

    pub fn arrow_schema(&self) -> arrow2::datatypes::Schema {
        arrow2::datatypes::Schema::from(self.arrow_fields())
    }

    /// Project (reorder / select) column indices into a new schema.
    pub fn project(&self, indices: &[usize]) -> QueryResult<Schema> {
        let mut fields = Vec::with_capacity(indices.len());
        for &i in indices {
            let f = self.fields.get(i).ok_or_else(|| {
                QueryError::invalid_input(format!("projection index {i} out of range"))
            })?;
            fields.push(f.clone());
        }
        Ok(Schema::new(fields))
    }
}

/// A single nullable value. The type is carried by the owning schema; this enum
/// is used for building fixtures and for row-level join/sort key comparison.
#[derive(Debug, Clone, PartialEq)]
pub enum Scalar {
    Int(Option<i64>),
    Utf8(Option<String>),
    Bool(Option<bool>),
}

impl Scalar {
    pub fn type_matches(&self, ty: ColumnType) -> bool {
        matches!(
            (self, ty),
            (Scalar::Int(_), ColumnType::Int)
                | (Scalar::Utf8(_), ColumnType::Utf8)
                | (Scalar::Bool(_), ColumnType::Bool)
        )
    }

    pub fn is_null(&self) -> bool {
        match self {
            Scalar::Int(v) => v.is_none(),
            Scalar::Utf8(v) => v.is_none(),
            Scalar::Bool(v) => v.is_none(),
        }
    }
}

/// An owned, typed columnar batch.
pub struct Batch {
    schema: Arc<Schema>,
    chunk: Chunk<Box<dyn Array>>,
}

impl Batch {
    /// Build a batch from a schema and already-constructed arrays, validating
    /// column count, types and equal length.
    pub fn try_new(schema: Arc<Schema>, arrays: Vec<Box<dyn Array>>) -> QueryResult<Self> {
        if arrays.len() != schema.len() {
            return Err(QueryError::invalid_input(format!(
                "batch has {} arrays but schema has {} fields",
                arrays.len(),
                schema.len()
            )));
        }
        for (i, (arr, (_, ty))) in arrays.iter().zip(schema.fields().iter()).enumerate() {
            let actual = ColumnType::from_arrow(arr.data_type())?;
            if actual != *ty {
                return Err(QueryError::invalid_input(format!(
                    "column {i} type mismatch: batch has {}, schema expects {}",
                    actual.as_str(),
                    ty.as_str()
                )));
            }
        }
        let rows = arrays.first().map(|a| a.len()).unwrap_or(0);
        for (i, a) in arrays.iter().enumerate() {
            if a.len() != rows {
                return Err(QueryError::invalid_input(format!(
                    "ragged batch: column 0 has {rows} rows but column {i} has {}",
                    a.len()
                )));
            }
        }
        Ok(Self {
            schema,
            chunk: Chunk::new(arrays),
        })
    }

    pub fn schema(&self) -> &Arc<Schema> {
        &self.schema
    }
    pub fn chunk(&self) -> &Chunk<Box<dyn Array>> {
        &self.chunk
    }
    pub fn columns(&self) -> &[Box<dyn Array>] {
        self.chunk.columns()
    }
    pub fn num_rows(&self) -> usize {
        self.chunk.len()
    }
    pub fn is_empty(&self) -> bool {
        self.num_rows() == 0
    }

    /// Materialize one column as row scalars. Used by sort keys, join keys and
    /// the independent reference oracle.
    pub fn column_scalars(&self, col: usize) -> QueryResult<Vec<Scalar>> {
        let arr = self
            .columns()
            .get(col)
            .ok_or_else(|| QueryError::invalid_input(format!("column {col} out of range")))?;
        array_to_scalars(arr.as_ref())
    }

    /// Reconstruct a batch selecting a subset/reordering of columns.
    pub fn project(&self, indices: &[usize]) -> QueryResult<Batch> {
        let cols = self.columns();
        let mut arrays = Vec::with_capacity(indices.len());
        for &i in indices {
            arrays.push(
                cols.get(i)
                    .ok_or_else(|| {
                        QueryError::invalid_input(format!("project index {i} out of range"))
                    })?
                    .clone()
                    .to_boxed(),
            );
        }
        Batch::try_new(Arc::new(self.schema.project(indices)?), arrays)
    }
}

impl std::fmt::Debug for Batch {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Batch")
            .field("rows", &self.num_rows())
            .field("schema", &self.schema)
            .finish()
    }
}

impl Clone for Batch {
    /// Clone the batch handle. Arrow arrays are immutable and reference
    /// counted, so this produces an independent `Chunk`/schema owner sharing the
    /// same column buffers. The clone stays readable even if the operator that
    /// produced the original errors, is cancelled, or closes — this is the
    /// returned-batch/execution-context separation in data terms.
    fn clone(&self) -> Self {
        let arrays = self.chunk.columns().iter().map(|a| a.to_boxed()).collect();
        Self {
            schema: self.schema.clone(),
            chunk: Chunk::new(arrays),
        }
    }
}

/// Row-oriented builder for fixtures and tests. Columns accumulate as scalars
/// and are frozen into Arrow arrays once, keeping the hot path type-checked.
pub struct BatchBuilder {
    schema: Arc<Schema>,
    columns: Vec<Vec<Scalar>>,
}

impl BatchBuilder {
    pub fn new(schema: Arc<Schema>) -> Self {
        let n = schema.len();
        Self {
            schema,
            columns: (0..n).map(|_| Vec::new()).collect(),
        }
    }

    pub fn with_capacity(schema: Arc<Schema>, cap: usize) -> Self {
        let n = schema.len();
        Self {
            schema,
            columns: (0..n).map(|_| Vec::with_capacity(cap)).collect(),
        }
    }

    pub fn add_row(&mut self, row: &[Scalar]) -> QueryResult<&mut Self> {
        if row.len() != self.schema.len() {
            return Err(QueryError::invalid_input(format!(
                "row has {} values but schema has {} fields",
                row.len(),
                self.schema.len()
            )));
        }
        for (i, value) in row.iter().enumerate() {
            let ty = self.schema.fields()[i].1;
            if !value.type_matches(ty) {
                return Err(QueryError::invalid_input(format!(
                    "column {} expects {} but got {value:?}",
                    i,
                    ty.as_str()
                )));
            }
            self.columns[i].push(value.clone());
        }
        Ok(self)
    }

    pub fn num_rows(&self) -> usize {
        self.columns.first().map(|c| c.len()).unwrap_or(0)
    }

    pub fn finish(self) -> QueryResult<Batch> {
        let arrays = self
            .columns
            .iter()
            .zip(self.schema.fields().iter())
            .map(|(values, (_, ty))| build_array(*ty, values))
            .collect::<QueryResult<Vec<_>>>()?;
        Batch::try_new(self.schema, arrays)
    }
}

fn build_array(ty: ColumnType, values: &[Scalar]) -> QueryResult<Box<dyn Array>> {
    match ty {
        ColumnType::Int => {
            let mut b = MutablePrimitiveArray::<i64>::with_capacity(values.len());
            for v in values {
                match v {
                    Scalar::Int(x) => b.push(*x),
                    other => {
                        return Err(QueryError::invalid_input(format!(
                            "expected int scalar, got {other:?}"
                        )))
                    }
                }
            }
            Ok(b.as_box())
        }
        ColumnType::Utf8 => {
            let mut b = MutableUtf8Array::<i32>::with_capacity(values.len());
            for v in values {
                match v {
                    Scalar::Utf8(x) => b.push(x.as_ref()),
                    other => {
                        return Err(QueryError::invalid_input(format!(
                            "expected utf8 scalar, got {other:?}"
                        )))
                    }
                }
            }
            Ok(b.as_box())
        }
        ColumnType::Bool => {
            let mut b = MutableBooleanArray::with_capacity(values.len());
            for v in values {
                match v {
                    Scalar::Bool(x) => b.push(*x),
                    other => {
                        return Err(QueryError::invalid_input(format!(
                            "expected bool scalar, got {other:?}"
                        )))
                    }
                }
            }
            Ok(b.as_box())
        }
    }
}

/// Downcast an Arrow array into row scalars, honoring null validity.
pub fn array_to_scalars(arr: &dyn Array) -> QueryResult<Vec<Scalar>> {
    match ColumnType::from_arrow(arr.data_type())? {
        ColumnType::Int => {
            let a = arr
                .as_any()
                .downcast_ref::<PrimitiveArray<i64>>()
                .ok_or_else(|| QueryError::computation("int downcast failed"))?;
            Ok(a.iter().map(|x| Scalar::Int(x.copied())).collect())
        }
        ColumnType::Utf8 => {
            let a = arr
                .as_any()
                .downcast_ref::<Utf8Array<i32>>()
                .ok_or_else(|| QueryError::computation("utf8 downcast failed"))?;
            Ok(a.iter()
                .map(|x| Scalar::Utf8(x.map(|s| s.to_string())))
                .collect())
        }
        ColumnType::Bool => {
            let a = arr
                .as_any()
                .downcast_ref::<BooleanArray>()
                .ok_or_else(|| QueryError::computation("bool downcast failed"))?;
            Ok(a.iter().map(Scalar::Bool).collect())
        }
    }
}
