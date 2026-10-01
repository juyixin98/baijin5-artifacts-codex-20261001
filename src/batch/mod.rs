//! Typed columnar batches backed by Arrow2 arrays.
//!
//! A [`RecordBatch`] owns an Arrow2 schema plus one Arrow2 array per column.
//! Row-oriented access is offered as a convenience for the small working-set
//! sizes the recursive engine deals with; the canonical storage is columnar.

use arrow2::array::{Array, BooleanArray, PrimitiveArray, Utf8Array};
use arrow2::chunk::Chunk;
use arrow2::datatypes::DataType as ArrowType;

use crate::error::{EngineError, Result};

mod schema;
mod value;

// These re-exports also bring the names into scope for this module.
pub use schema::{check_value, DataType, Field, Schema};
pub use value::Value;

/// A columnar table fragment: Arrow2 arrays sharing one row count.
#[derive(Clone, Debug)]
pub struct RecordBatch {
    schema: Schema,
    columns: Vec<Box<dyn Array>>,
    num_rows: usize,
}

impl RecordBatch {
    /// Build a batch from raw Arrow2 columns, validating type and length.
    pub fn try_new(schema: Schema, columns: Vec<Box<dyn Array>>) -> Result<Self> {
        if columns.len() != schema.column_count() {
            return Err(EngineError::validation(format!(
                "batch has {} columns but schema declares {}",
                columns.len(),
                schema.column_count()
            )));
        }
        let num_rows = columns.first().map(|c| c.len()).unwrap_or(0);
        for (field, col) in schema.fields().iter().zip(columns.iter()) {
            if col.len() != num_rows {
                return Err(EngineError::validation(format!(
                    "column '{}' has {} rows, expected {}",
                    field.name,
                    col.len(),
                    num_rows
                )));
            }
            let expected = arrow_type(field.data_type);
            if col.data_type() != &expected {
                return Err(EngineError::validation(format!(
                    "column '{}' has arrow type {:?}, expected {:?}",
                    field.name,
                    col.data_type(),
                    expected
                )));
            }
        }
        Ok(Self {
            schema,
            columns,
            num_rows,
        })
    }

    /// Build a batch row-by-row. Every row is type-checked against `schema`.
    pub fn from_rows(schema: Schema, rows: Vec<Vec<Value>>) -> Result<Self> {
        let mut builders: Vec<Vec<Option<Value>>> = (0..schema.column_count())
            .map(|_| Vec::with_capacity(rows.len()))
            .collect();
        for (row_idx, row) in rows.into_iter().enumerate() {
            if row.len() != schema.column_count() {
                return Err(EngineError::validation(format!(
                    "row {row_idx} has {} values, schema expects {}",
                    row.len(),
                    schema.column_count()
                )));
            }
            for (col_idx, (value, field)) in row.into_iter().zip(schema.fields().iter()).enumerate()
            {
                check_value(&value, field.data_type)?;
                let packed = match value {
                    Value::Null => None,
                    non_null => Some(non_null),
                };
                builders[col_idx].push(packed);
            }
        }
        let columns = schema
            .fields()
            .iter()
            .zip(builders)
            .map(|(field, values)| build_array(field.data_type, values))
            .collect();
        Self::try_new(schema, columns)
    }

    /// Zero-row batch with the given schema.
    pub fn empty(schema: Schema) -> Self {
        let columns = schema
            .fields()
            .iter()
            .map(|f| empty_array(f.data_type))
            .collect();
        Self::try_new(schema, columns).expect("empty batch is always valid")
    }

    /// Batch schema.
    pub fn schema(&self) -> &Schema {
        &self.schema
    }

    /// Underlying Arrow2 columns.
    pub fn columns(&self) -> &[Box<dyn Array>] {
        &self.columns
    }

    /// Number of rows.
    pub fn num_rows(&self) -> usize {
        self.num_rows
    }

    /// `true` when the batch holds no rows.
    pub fn is_empty(&self) -> bool {
        self.num_rows == 0
    }

    /// Read one row as owned values.
    pub fn row(&self, index: usize) -> Vec<Value> {
        assert!(index < self.num_rows, "row index {index} out of range");
        self.schema
            .fields()
            .iter()
            .zip(&self.columns)
            .map(|(field, col)| read_value(col.as_ref(), field.data_type, index))
            .collect()
    }

    /// All rows as owned values (used by tests and reference comparisons).
    pub fn to_rows(&self) -> Vec<Vec<Value>> {
        (0..self.num_rows).map(|i| self.row(i)).collect()
    }

    /// Project a subset of rows by index, preserving the given order.
    pub fn take_rows(&self, indices: &[usize]) -> Result<Self> {
        let rows: Vec<Vec<Value>> = indices
            .iter()
            .map(|&i| {
                if i >= self.num_rows {
                    return Err(EngineError::validation(format!(
                        "take_rows: index {i} out of range ({} rows)",
                        self.num_rows
                    )));
                }
                Ok(self.row(i))
            })
            .collect::<Result<_>>()?;
        Self::from_rows(self.schema.clone(), rows)
    }

    /// Concatenate `batches`, which must all share an identical schema.
    pub fn concat_all(batches: &[RecordBatch]) -> Result<RecordBatch> {
        let mut iter = batches.iter();
        let first = iter
            .next()
            .ok_or_else(|| EngineError::validation("concat_all requires at least one batch"))?;
        let schema = first.schema().clone();
        for b in iter {
            if b.schema() != &schema {
                return Err(EngineError::validation(
                    "concat_all: schemas do not match exactly",
                ));
            }
        }
        let mut rows: Vec<Vec<Value>> = Vec::new();
        for b in batches {
            rows.extend(b.to_rows());
        }
        Self::from_rows(schema, rows)
    }

    /// Convert to an Arrow2 [`Chunk`] (the typed Arrow2 schema stays available
    /// via [`Self::schema`]; an arrow2 `Chunk` itself carries only arrays).
    pub fn to_arrow_chunk(&self) -> Chunk<Box<dyn Array>> {
        Chunk::new(self.columns.clone())
    }
}

fn arrow_type(ty: DataType) -> ArrowType {
    match ty {
        DataType::Int64 => ArrowType::Int64,
        DataType::Utf8 => ArrowType::Utf8,
        DataType::Boolean => ArrowType::Boolean,
    }
}

fn empty_array(ty: DataType) -> Box<dyn Array> {
    match ty {
        DataType::Int64 => Box::new(PrimitiveArray::<i64>::from(Vec::<Option<i64>>::new())),
        DataType::Utf8 => Box::new(Utf8Array::<i32>::from(Vec::<Option<&str>>::new())),
        DataType::Boolean => Box::new(BooleanArray::from(Vec::<Option<bool>>::new())),
    }
}

fn build_array(ty: DataType, values: Vec<Option<Value>>) -> Box<dyn Array> {
    match ty {
        DataType::Int64 => {
            let opts: Vec<Option<i64>> = values
                .into_iter()
                .map(|v| match v {
                    None | Some(Value::Null) => None,
                    Some(Value::Int64(n)) => Some(n),
                    other => panic!("type checker allowed bad value: {other:?}"),
                })
                .collect();
            Box::new(PrimitiveArray::<i64>::from(opts))
        }
        DataType::Utf8 => {
            let opts: Vec<Option<String>> = values
                .into_iter()
                .map(|v| match v {
                    None | Some(Value::Null) => None,
                    Some(Value::Utf8(s)) => Some(s),
                    other => panic!("type checker allowed bad value: {other:?}"),
                })
                .collect();
            Box::new(Utf8Array::<i32>::from(opts))
        }
        DataType::Boolean => {
            let opts: Vec<Option<bool>> = values
                .into_iter()
                .map(|v| match v {
                    None | Some(Value::Null) => None,
                    Some(Value::Boolean(b)) => Some(b),
                    other => panic!("type checker allowed bad value: {other:?}"),
                })
                .collect();
            Box::new(BooleanArray::from(opts))
        }
    }
}

fn read_value(array: &dyn Array, ty: DataType, index: usize) -> Value {
    if array.is_null(index) {
        return Value::Null;
    }
    match ty {
        DataType::Int64 => {
            let a = array
                .as_any()
                .downcast_ref::<PrimitiveArray<i64>>()
                .unwrap();
            Value::Int64(a.value(index))
        }
        DataType::Utf8 => {
            let a = array.as_any().downcast_ref::<Utf8Array<i32>>().unwrap();
            Value::Utf8(a.value(index).to_owned())
        }
        DataType::Boolean => {
            let a = array.as_any().downcast_ref::<BooleanArray>().unwrap();
            Value::Boolean(a.value(index))
        }
    }
}
