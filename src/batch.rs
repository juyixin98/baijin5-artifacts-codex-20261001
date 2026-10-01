//! Typed Arrow2 batches.
//!
//! Rows produced by the engine are converted into a single Arrow
//! [`Chunk`] with one nullable, explicitly-typed column per output attribute,
//! and can be serialised to the Arrow IPC streaming format for binary
//! transport. Typed columns (not `Vec<Vec<serde_json::Value>>`) are the
//! contract of the binary endpoint; JSON is only a convenience view.

use arrow2::array::{Array, BooleanArray, PrimitiveArray, Utf8Array};
use arrow2::chunk::Chunk as ArrowChunk;
use arrow2::datatypes::{DataType, Field, Schema};
use arrow2::io::ipc::write::{StreamWriter, WriteOptions};

use crate::error::{ErrorCode, Result, ServiceError};
use crate::schema::ColumnType;
use crate::value::Cell;

/// One output column: its field and typed array.
pub struct TypedChunk {
    pub schema: Schema,
    pub chunk: ArrowChunk<Box<dyn Array>>,
}

impl std::fmt::Debug for TypedChunk {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("TypedChunk")
            .field("schema", &self.schema)
            .field("num_rows", &self.chunk.len())
            .finish()
    }
}

impl TypedChunk {
    /// Build a typed chunk from rows that all follow `columns`.
    pub fn from_rows(columns: &[(String, ColumnType)], rows: &[Vec<Cell>]) -> Result<Self> {
        let mut fields = Vec::with_capacity(columns.len());
        let mut arrays: Vec<Box<dyn Array>> = Vec::with_capacity(columns.len());

        for (col_index, (name, typ)) in columns.iter().enumerate() {
            let (field, array): (Field, Box<dyn Array>) = match typ {
                ColumnType::Int => {
                    let opts: Vec<Option<i64>> = rows
                        .iter()
                        .map(|row| match &row[col_index] {
                            Cell::Value(crate::value::Scalar::Int(i)) => Some(*i),
                            Cell::Null => None,
                            other => panic!("validated int cell, got {other:?}"),
                        })
                        .collect();
                    (
                        Field::new(name.clone(), DataType::Int64, true),
                        Box::new(PrimitiveArray::<i64>::from(opts)),
                    )
                }
                ColumnType::Str => {
                    let opts: Vec<Option<&str>> = rows
                        .iter()
                        .map(|row| match &row[col_index] {
                            Cell::Value(crate::value::Scalar::Str(s)) => Some(s.as_str()),
                            Cell::Null => None,
                            other => panic!("validated string cell, got {other:?}"),
                        })
                        .collect();
                    (
                        Field::new(name.clone(), DataType::Utf8, true),
                        Box::new(Utf8Array::<i32>::from_iter(opts)),
                    )
                }
                ColumnType::Bool => {
                    let opts: Vec<Option<bool>> = rows
                        .iter()
                        .map(|row| match &row[col_index] {
                            Cell::Value(crate::value::Scalar::Bool(b)) => Some(*b),
                            Cell::Null => None,
                            other => panic!("validated bool cell, got {other:?}"),
                        })
                        .collect();
                    (
                        Field::new(name.clone(), DataType::Boolean, true),
                        Box::new(BooleanArray::from(opts)),
                    )
                }
            };
            fields.push(field);
            arrays.push(array);
        }

        let schema = Schema::from(fields);
        let chunk = ArrowChunk::new(arrays);
        Ok(TypedChunk { schema, chunk })
    }

    pub fn num_rows(&self) -> usize {
        self.chunk.len()
    }

    /// Serialise as an Arrow IPC stream (schema message followed by record
    /// batches followed by EOS).
    pub fn to_ipc_stream(&self) -> Result<Vec<u8>> {
        let mut buf = Vec::new();
        let mut writer = StreamWriter::new(&mut buf, WriteOptions { compression: None });
        writer.start(&self.schema, None).map_err(|e| {
            ServiceError::new(ErrorCode::Internal, format!("ipc start failed: {e}"))
        })?;
        writer.write(&self.chunk, None).map_err(|e| {
            ServiceError::new(ErrorCode::Internal, format!("ipc write failed: {e}"))
        })?;
        writer.finish().map_err(|e| {
            ServiceError::new(ErrorCode::Internal, format!("ipc finish failed: {e}"))
        })?;
        Ok(buf)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::value::Scalar;

    #[test]
    fn builds_typed_columns_preserving_nulls() {
        let columns = vec![
            ("a".to_string(), ColumnType::Int),
            ("s".to_string(), ColumnType::Str),
            ("f".to_string(), ColumnType::Bool),
        ];
        let rows = vec![
            vec![
                Cell::Value(Scalar::Int(7)),
                Cell::Value(Scalar::Str("x".to_string())),
                Cell::Value(Scalar::Bool(true)),
            ],
            vec![Cell::Null, Cell::Null, Cell::Null],
        ];
        let typed = TypedChunk::from_rows(&columns, &rows).unwrap();
        assert_eq!(typed.num_rows(), 2);
        assert_eq!(typed.schema.fields.len(), 3);
        assert_eq!(typed.schema.fields[0].data_type(), &DataType::Int64);

        let ipc = typed.to_ipc_stream().unwrap();
        assert!(ipc.len() > 16, "ipc stream must carry schema and data");
    }
}
