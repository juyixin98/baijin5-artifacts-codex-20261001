//! Inline JSON rows -> [`TypedBatch`] for the HTTP entrypoint.
//!
//! The same type rules as fixtures apply: JSON null is SQL NULL, numbers must
//! fit the declared type (`bigint`: exact i64; `double`: any JSON number),
//! booleans for boolean columns, strings for text. Type mismatches are input
//! errors with a column position, never silently coerced.

use arrow2::array::{Array, BooleanArray, PrimitiveArray, Utf8Array};
use arrow2::chunk::Chunk;

use crate::batch::TypedBatch;
use crate::batch::schema::{LogicalType, Schema};
use crate::error::{InputCode, Result, SetOpsError};

/// Build one batch from schema-ordered JSON rows. Rows are validated against
/// the schema; values are returned as Arrow2 arrays.
pub fn build_batch(schema: &Schema, rows: &[Vec<serde_json::Value>]) -> Result<TypedBatch> {
    let ncols = schema.len();
    let mut builders: Vec<JsonColumn> = schema
        .fields()
        .iter()
        .map(|f| JsonColumn::new(f.ty, rows.len()))
        .collect();

    for (ri, row) in rows.iter().enumerate() {
        if row.len() != ncols {
            return Err(SetOpsError::input(
                InputCode::ColumnCountMismatch,
                format!(
                    "row {} has {} values, schema has {ncols}",
                    ri + 1,
                    row.len()
                ),
            ));
        }
        for (ci, (field, v)) in schema.fields().iter().zip(row).enumerate() {
            builders[ci].push(v).map_err(|m| {
                SetOpsError::input(InputCode::ParseValue, m)
                    .ctx(|c| c.at(format!("row {}, column '{}'", ri + 1, field.name)))
            })?;
        }
    }

    let arrays: Vec<Box<dyn Array>> = builders.into_iter().map(|b| b.finish()).collect();
    let chunk = Chunk::try_new(arrays)
        .map_err(|e| SetOpsError::compute(format!("arrow chunk error: {e}")))?;
    Ok(TypedBatch::new(schema.clone(), chunk))
}

enum JsonColumn {
    BigInt(Vec<Option<i64>>),
    Double(Vec<Option<f64>>),
    Boolean(Vec<Option<bool>>),
    Text(Vec<Option<String>>),
}

impl JsonColumn {
    fn new(ty: LogicalType, cap: usize) -> Self {
        match ty {
            LogicalType::BigInt => Self::BigInt(Vec::with_capacity(cap)),
            LogicalType::Double => Self::Double(Vec::with_capacity(cap)),
            LogicalType::Boolean => Self::Boolean(Vec::with_capacity(cap)),
            LogicalType::Text => Self::Text(Vec::with_capacity(cap)),
        }
    }

    fn push(&mut self, v: &serde_json::Value) -> std::result::Result<(), String> {
        match (self, v) {
            (s, serde_json::Value::Null) => match s {
                Self::BigInt(x) => x.push(None),
                Self::Double(x) => x.push(None),
                Self::Boolean(x) => x.push(None),
                Self::Text(x) => x.push(None),
            },
            (Self::BigInt(x), serde_json::Value::Number(n)) => x.push(Some(
                n.as_i64()
                    .ok_or_else(|| format!("{n} is not an exact bigint"))?,
            )),
            (Self::Double(x), serde_json::Value::Number(n)) => x
                .push(Some(n.as_f64().ok_or_else(|| {
                    format!("{n} is not representable as double")
                })?)),
            (Self::Boolean(x), serde_json::Value::Bool(b)) => x.push(Some(*b)),
            (Self::Text(x), serde_json::Value::String(s)) => x.push(Some(s.clone())),
            (_, got) => return Err(format!("type mismatch, got JSON {got}")),
        }
        Ok(())
    }

    fn finish(self) -> Box<dyn Array> {
        match self {
            Self::BigInt(x) => Box::new(PrimitiveArray::<i64>::from(x)),
            Self::Double(x) => Box::new(PrimitiveArray::<f64>::from(x)),
            Self::Boolean(x) => Box::new(BooleanArray::from(x)),
            Self::Text(x) => Box::new(Utf8Array::<i32>::from(x)),
        }
    }
}

/// Render a batch back to JSON rows for API responses.
pub fn batch_to_json(batch: &TypedBatch) -> Vec<Vec<serde_json::Value>> {
    use arrow2::array::{BooleanArray, PrimitiveArray, Utf8Array};
    let n = batch.rows();
    let arrays = batch.chunk.arrays();
    let mut rows = vec![Vec::with_capacity(arrays.len()); n];
    // Arrow2 stores columns; we populate row-major output by column index, so
    // the index loop is load-bearing here.
    #[allow(clippy::needless_range_loop)]
    for (ci, field) in batch.schema.fields().iter().enumerate() {
        let a = arrays[ci].as_ref();
        for ri in 0..n {
            let val = if a.is_null(ri) {
                serde_json::Value::Null
            } else {
                match field.ty {
                    LogicalType::BigInt => {
                        let p = a.as_any().downcast_ref::<PrimitiveArray<i64>>().unwrap();
                        serde_json::json!(p.value(ri))
                    }
                    LogicalType::Double => {
                        let p = a.as_any().downcast_ref::<PrimitiveArray<f64>>().unwrap();
                        serde_json::json!(p.value(ri))
                    }
                    LogicalType::Boolean => {
                        let p = a.as_any().downcast_ref::<BooleanArray>().unwrap();
                        serde_json::json!(p.value(ri))
                    }
                    LogicalType::Text => {
                        let p = a.as_any().downcast_ref::<Utf8Array<i32>>().unwrap();
                        serde_json::json!(p.value(ri))
                    }
                }
            };
            rows[ri].push(val);
        }
    }
    rows
}
