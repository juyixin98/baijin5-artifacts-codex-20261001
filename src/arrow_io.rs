//! Arrow2 boundary: encode typed output rows into columnar chunks and an IPC
//! stream. The engine works on [`Datum`]; this module is the only place that
//! knows about arrow data types.
use arrow2::array::{Array, MutableBooleanArray, MutablePrimitiveArray, MutableUtf8Array};
use arrow2::chunk::Chunk;
use arrow2::datatypes::{DataType, Field, Schema};
use arrow2::io::ipc::write::{self, StreamWriter};

use crate::domain::{Datum, LogicalType};
use crate::error::{ErrorCode, JoinError, JoinResult};
use crate::join::EmittedRow;

pub fn to_arrow_data_type(ty: LogicalType) -> DataType {
    match ty {
        LogicalType::Int64 => DataType::Int64,
        LogicalType::Utf8 => DataType::Utf8,
        LogicalType::Boolean => DataType::Boolean,
    }
}

/// Build the arrow schema for `(attribute, type)` pairs. Columns are nullable:
/// private attributes may legitimately carry NULL.
pub fn build_arrow_schema(columns: &[(String, LogicalType)]) -> Schema {
    let fields: Vec<Field> = columns
        .iter()
        .map(|(name, ty)| Field::new(name.clone(), to_arrow_data_type(*ty), true))
        .collect();
    Schema::from(fields)
}

fn build_column(values: &[Datum], ty: LogicalType) -> JoinResult<Box<dyn Array>> {
    let arr: Box<dyn Array> = match ty {
        LogicalType::Int64 => {
            let mut b = MutablePrimitiveArray::<i64>::with_capacity(values.len());
            for v in values {
                match v {
                    Datum::Int(i) => b.push(Some(*i)),
                    Datum::Null => b.push(None),
                    _ => return Err(type_err()),
                }
            }
            let a: arrow2::array::PrimitiveArray<i64> = b.into();
            Box::new(a)
        }
        LogicalType::Boolean => {
            let mut b = MutableBooleanArray::with_capacity(values.len());
            for v in values {
                match v {
                    Datum::Bool(x) => b.push(Some(*x)),
                    Datum::Null => b.push(None),
                    _ => return Err(type_err()),
                }
            }
            let a: arrow2::array::BooleanArray = b.into();
            Box::new(a)
        }
        LogicalType::Utf8 => {
            let mut b = MutableUtf8Array::<i32>::with_capacity(values.len());
            for v in values {
                match v {
                    Datum::Str(s) => b.push(Some(s.as_str())),
                    Datum::Null => b.push(None::<&str>),
                    _ => return Err(type_err()),
                }
            }
            let a: arrow2::array::Utf8Array<i32> = b.into();
            Box::new(a)
        }
    };
    Ok(arr)
}

fn type_err() -> JoinError {
    JoinError::new(
        ErrorCode::EncodeFailed,
        "output value did not match its declared arrow type",
    )
}

/// Assemble one chunk whose columns follow `columns` order.
pub fn rows_to_chunk(
    rows: &[EmittedRow],
    columns: &[(String, LogicalType)],
) -> JoinResult<Chunk<Box<dyn Array>>> {
    let width = columns.len();
    let mut col_values: Vec<Vec<Datum>> =
        (0..width).map(|_| Vec::with_capacity(rows.len())).collect();
    for row in rows {
        for (i, v) in row.values.iter().enumerate() {
            col_values[i].push(v.clone());
        }
    }
    let arrays = columns
        .iter()
        .enumerate()
        .map(|(i, (_, ty))| build_column(&col_values[i], *ty))
        .collect::<JoinResult<Vec<_>>>()?;
    Ok(Chunk::new(arrays))
}

/// Serialize rows to a streaming Arrow IPC buffer.
pub fn encode_ipc(rows: &[EmittedRow], columns: &[(String, LogicalType)]) -> JoinResult<Vec<u8>> {
    let schema = build_arrow_schema(columns);
    let chunk = rows_to_chunk(rows, columns)?;
    let mut buf: Vec<u8> = Vec::new();
    let options = write::WriteOptions { compression: None };
    let mut writer = StreamWriter::new(&mut buf, options);
    writer
        .start(&schema, None)
        .map_err(|e| JoinError::new(ErrorCode::EncodeFailed, format!("ipc start: {e}")))?;
    writer
        .write(&chunk, None)
        .map_err(|e| JoinError::new(ErrorCode::EncodeFailed, format!("ipc write: {e}")))?;
    writer
        .finish()
        .map_err(|e| JoinError::new(ErrorCode::EncodeFailed, format!("ipc finish: {e}")))?;
    Ok(buf)
}
