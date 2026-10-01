//! Arrow2 boundary.
//!
//! Result batches are converted to Arrow2 arrays ([`Batch::to_arrow`]); the
//! JSON rows returned over HTTP are read **back out of the Arrow arrays**, so
//! Arrow2 is the real output representation rather than a decorative type.
//! A round-trip comparison guards the conversion.

use arrow2::array::{Array, PrimitiveArray, Utf8Array};
use arrow2::chunk::Chunk;
use serde_json::{json, Value};

use crate::batch::Batch;
use crate::error::{ErrorKind, QError, QResult};

/// Human/JSON name of the physical Arrow type of each output column.
pub fn arrow_type_names(chunk: &Chunk<Box<dyn Array>>) -> Vec<String> {
    chunk
        .iter()
        .map(|a| match a.data_type() {
            arrow2::datatypes::DataType::Int64 => "Int64".to_string(),
            arrow2::datatypes::DataType::Utf8 => "Utf8".to_string(),
            other => format!("{other:?}"),
        })
        .collect()
}

/// Serialize a chunk to row-oriented JSON by reading values from the Arrow
/// arrays (not from the source batch).
pub fn chunk_to_json_rows(chunk: &Chunk<Box<dyn Array>>) -> QResult<Vec<Vec<Value>>> {
    let ncols = chunk.len();
    let nrows = chunk.first().map(|a| a.len()).unwrap_or(0);
    let mut rows: Vec<Vec<Value>> = (0..nrows).map(|_| Vec::with_capacity(ncols)).collect();

    for array in chunk.iter() {
        match array.data_type() {
            arrow2::datatypes::DataType::Int64 => {
                let a = array
                    .as_any()
                    .downcast_ref::<PrimitiveArray<i64>>()
                    .ok_or_else(|| arrow_malformed("Int64 column is not a PrimitiveArray<i64>"))?;
                for (row, v) in a.iter().enumerate() {
                    rows[row].push(match v {
                        Some(i) => json!(i),
                        None => Value::Null,
                    });
                }
            }
            arrow2::datatypes::DataType::Utf8 => {
                let a = array
                    .as_any()
                    .downcast_ref::<Utf8Array<i32>>()
                    .ok_or_else(|| arrow_malformed("Utf8 column is not a Utf8Array<i32>"))?;
                for (row, v) in a.iter().enumerate() {
                    rows[row].push(match v {
                        Some(s) => json!(s),
                        None => Value::Null,
                    });
                }
            }
            other => {
                return Err(arrow_malformed(&format!(
                    "unsupported arrow output type {other:?}"
                )));
            }
        }
    }
    Ok(rows)
}

fn arrow_malformed(msg: &str) -> QError {
    QError::new(ErrorKind::MalformedBatch, format!("arrow boundary: {msg}"))
}

/// Full output conversion: typed batch -> Arrow chunk -> JSON rows, plus the
/// physical Arrow type names.
pub fn encode_result(batch: &Batch) -> QResult<EncodedResult> {
    let chunk = batch.to_arrow();
    let arrow_types = arrow_type_names(&chunk);
    let rows = chunk_to_json_rows(&chunk)?;
    verify_roundtrip(batch, &rows)?;
    Ok(EncodedResult { arrow_types, rows })
}

#[derive(Debug, Clone)]
pub struct EncodedResult {
    pub arrow_types: Vec<String>,
    pub rows: Vec<Vec<Value>>,
}

/// Confirm the Arrow-derived JSON matches the logical scalars of the batch.
fn verify_roundtrip(batch: &Batch, rows: &[Vec<Value>]) -> QResult<()> {
    if rows.len() != batch.row_count() {
        return Err(arrow_malformed("round-trip row count mismatch"));
    }
    for (ri, row) in rows.iter().enumerate() {
        if row.len() != batch.schema().fields.len() {
            return Err(arrow_malformed("round-trip column count mismatch"));
        }
        for (ci, cell) in row.iter().enumerate() {
            let scalar = batch.get(ci, ri);
            let ok = match (scalar, cell) {
                (crate::batch::Scalar::Null, Value::Null) => true,
                (crate::batch::Scalar::Int(i), Value::Number(n)) => n.as_i64() == Some(*i),
                (crate::batch::Scalar::Str(s), Value::String(t)) => &**s == t,
                _ => false,
            };
            if !ok {
                return Err(arrow_malformed(&format!(
                    "round-trip mismatch at row {ri} col {ci}"
                )));
            }
        }
    }
    Ok(())
}
