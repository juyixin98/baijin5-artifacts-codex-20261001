//! Bridge between the engine's [`Value`] rows and Arrow2 typed batches.
//!
//! A *typed batch* is an [`arrow2::chunk::Chunk`] whose per-column Arrow type is
//! derived from the logical [`Schema`]. Lists and strings use 64-bit offsets
//! (`LargeList`/`LargeUtf8`) so a single batch cannot overflow 32-bit offsets.
//! NULL is represented with Arrow validity bitmaps at every nesting level.
//!
//! Conversion is total and schema-directed in both directions; it is covered
//! by round-trip tests (including nested NULLs).

use arrow2::array::{Array, BooleanArray, ListArray, PrimitiveArray, StructArray, Utf8Array};
use arrow2::bitmap::Bitmap;
use arrow2::buffer::Buffer;
use arrow2::chunk::Chunk;
use arrow2::datatypes::{DataType as ADataType, Field as AField, Schema as ASchema};
use arrow2::offset::OffsetsBuffer;

use crate::error::{Result, SetOpError};
use crate::value::{DataType, Field, Schema, Value};

/// Logical type -> Arrow type.
pub fn to_arrow_type(ty: &DataType) -> ADataType {
    match ty {
        DataType::Int64 => ADataType::Int64,
        DataType::Utf8 => ADataType::LargeUtf8,
        DataType::Bool => ADataType::Boolean,
        DataType::List(inner) => ADataType::LargeList(Box::new(to_arrow_field(inner))),
        DataType::Struct { fields: fs } => {
            ADataType::Struct(fs.iter().map(to_arrow_field).collect())
        }
    }
}

pub fn to_arrow_field(f: &Field) -> AField {
    AField::new(f.name.clone(), to_arrow_type(&f.data_type), f.nullable)
}

pub fn to_arrow_schema(schema: &Schema) -> ASchema {
    ASchema::from(
        schema
            .fields
            .iter()
            .map(to_arrow_field)
            .collect::<Vec<AField>>(),
    )
}

/// Build one Arrow column from the values at a fixed row position.
pub fn values_to_array(ty: &DataType, values: &[Value]) -> Result<Box<dyn Array>> {
    match ty {
        DataType::Int64 => {
            let mut validity = Vec::with_capacity(values.len());
            let mut vals = Vec::with_capacity(values.len());
            for v in values {
                match v {
                    Value::Null => {
                        validity.push(false);
                        vals.push(0);
                    }
                    Value::Int64(i) => {
                        validity.push(true);
                        vals.push(*i);
                    }
                    other => return Err(type_err("int64", other)),
                }
            }
            Ok(PrimitiveArray::<i64>::try_new(
                ADataType::Int64,
                Buffer::from(vals),
                Some(Bitmap::from_iter(validity)),
            )
            .map_err(|e| SetOpError::compute("arrow_build", e.to_string()))?
            .boxed())
        }
        DataType::Bool => {
            let mut validity = Vec::with_capacity(values.len());
            let mut vals = Vec::with_capacity(values.len());
            for v in values {
                match v {
                    Value::Null => {
                        validity.push(false);
                        vals.push(false);
                    }
                    Value::Bool(b) => {
                        validity.push(true);
                        vals.push(*b);
                    }
                    other => return Err(type_err("bool", other)),
                }
            }
            Ok(BooleanArray::try_new(
                ADataType::Boolean,
                Bitmap::from_iter(vals),
                Some(Bitmap::from_iter(validity)),
            )
            .map_err(|e| SetOpError::compute("arrow_build", e.to_string()))?
            .boxed())
        }
        DataType::Utf8 => {
            let mut offsets: Vec<i64> = Vec::with_capacity(values.len() + 1);
            let mut bytes: Vec<u8> = Vec::new();
            let mut validity = Vec::with_capacity(values.len());
            offsets.push(0);
            for v in values {
                match v {
                    Value::Null => {
                        validity.push(false);
                        offsets.push(bytes.len() as i64);
                    }
                    Value::Utf8(s) => {
                        validity.push(true);
                        bytes.extend_from_slice(s.as_bytes());
                        offsets.push(bytes.len() as i64);
                    }
                    other => return Err(type_err("utf8", other)),
                }
            }
            Ok(Utf8Array::<i64>::try_new(
                ADataType::LargeUtf8,
                OffsetsBuffer::try_from(Buffer::from(offsets))
                    .map_err(|e| SetOpError::compute("offset_error", e.to_string()))?,
                Buffer::from(bytes),
                Some(Bitmap::from_iter(validity)),
            )
            .map_err(|e| SetOpError::compute("arrow_build", e.to_string()))?
            .boxed())
        }
        DataType::List(inner) => {
            let mut offsets: Vec<i64> = Vec::with_capacity(values.len() + 1);
            let mut validity = Vec::with_capacity(values.len());
            let mut flat: Vec<Value> = Vec::new();
            offsets.push(0);
            for v in values {
                match v {
                    Value::Null => {
                        validity.push(false);
                        offsets.push(flat.len() as i64);
                    }
                    Value::List(items) => {
                        validity.push(true);
                        flat.extend(items.iter().cloned());
                        offsets.push(flat.len() as i64);
                    }
                    other => return Err(type_err("list", other)),
                }
            }
            let child = values_to_array(&inner.data_type, &flat)?;
            Ok(ListArray::<i64>::try_new(
                to_arrow_type(ty),
                OffsetsBuffer::try_from(Buffer::from(offsets))
                    .map_err(|e| SetOpError::compute("offset_error", e.to_string()))?,
                child,
                Some(Bitmap::from_iter(validity)),
            )
            .map_err(|e| SetOpError::compute("arrow_build", e.to_string()))?
            .boxed())
        }
        DataType::Struct { fields: field_defs } => {
            let mut validity = Vec::with_capacity(values.len());
            // Per-child accumulated values; NULL structs contribute NULL to
            // each child (struct validity carries the null-ness).
            let mut child_cols: Vec<Vec<Value>> = field_defs
                .iter()
                .map(|_| Vec::with_capacity(values.len()))
                .collect();
            for v in values {
                match v {
                    Value::Null => {
                        validity.push(false);
                        for col in child_cols.iter_mut() {
                            col.push(Value::Null);
                        }
                    }
                    Value::Struct(pairs) => {
                        if pairs.len() != field_defs.len() {
                            return Err(SetOpError::compute(
                                "struct_arity",
                                "struct value arity does not match type during arrow build",
                            ));
                        }
                        validity.push(true);
                        for (col, (_, fv)) in child_cols.iter_mut().zip(pairs.iter()) {
                            col.push(fv.clone());
                        }
                    }
                    other => return Err(type_err("struct", other)),
                }
            }
            let mut arrays = Vec::with_capacity(field_defs.len());
            for (fd, col) in field_defs.iter().zip(child_cols) {
                arrays.push(values_to_array(&fd.data_type, &col)?);
            }
            Ok(
                StructArray::try_new(to_arrow_type(ty), arrays, Some(Bitmap::from_iter(validity)))
                    .map_err(|e| SetOpError::compute("arrow_build", e.to_string()))?
                    .boxed(),
            )
        }
    }
}

fn type_err(expected: &str, got: &Value) -> SetOpError {
    SetOpError::input(
        "type_mismatch",
        format!(
            "value does not match Arrow column type {expected}: {:?}",
            value_kind(got)
        ),
    )
}

fn value_kind(v: &Value) -> &'static str {
    match v {
        Value::Null => "null",
        Value::Bool(_) => "bool",
        Value::Int64(_) => "int64",
        Value::Utf8(_) => "utf8",
        Value::List(_) => "list",
        Value::Struct(_) => "struct",
    }
}

/// Rows -> typed batch.
pub fn rows_to_chunk(schema: &Schema, rows: &[Vec<Value>]) -> Result<Chunk<Box<dyn Array>>> {
    let ncols = schema.fields.len();
    let mut columns = Vec::with_capacity(ncols);
    for (ci, f) in schema.fields.iter().enumerate() {
        let col_values: Vec<Value> = rows
            .iter()
            .map(|r| r.get(ci).cloned().unwrap_or(Value::Null))
            .collect();
        columns.push(values_to_array(&f.data_type, &col_values)?);
    }
    Chunk::try_new(columns).map_err(|e| SetOpError::compute("chunk_error", e.to_string()))
}

/// Read the value at logical index `i` from an Arrow array of type `ty`.
pub fn array_value(ty: &DataType, array: &dyn Array, i: usize) -> Result<Value> {
    if array.is_null(i) {
        return Ok(Value::Null);
    }
    match ty {
        DataType::Int64 => {
            let a = array
                .as_any()
                .downcast_ref::<PrimitiveArray<i64>>()
                .ok_or_else(|| downcast_err("int64"))?;
            Ok(Value::Int64(a.value(i)))
        }
        DataType::Bool => {
            let a = array
                .as_any()
                .downcast_ref::<BooleanArray>()
                .ok_or_else(|| downcast_err("bool"))?;
            Ok(Value::Bool(a.value(i)))
        }
        DataType::Utf8 => {
            let a = array
                .as_any()
                .downcast_ref::<Utf8Array<i64>>()
                .ok_or_else(|| downcast_err("largeutf8"))?;
            Ok(Value::Utf8(a.value(i).to_string()))
        }
        DataType::List(inner) => {
            let a = array
                .as_any()
                .downcast_ref::<ListArray<i64>>()
                .ok_or_else(|| downcast_err("largelist"))?;
            let offsets = a.offsets().buffer();
            let start = offsets[i] as usize;
            let end = offsets[i + 1] as usize;
            let child = a.values().as_ref();
            let mut items = Vec::with_capacity(end - start);
            for idx in start..end {
                items.push(array_value(&inner.data_type, child, idx)?);
            }
            Ok(Value::List(items))
        }
        DataType::Struct { fields: field_defs } => {
            let a = array
                .as_any()
                .downcast_ref::<StructArray>()
                .ok_or_else(|| downcast_err("struct"))?;
            let mut pairs = Vec::with_capacity(field_defs.len());
            for (fi, fd) in field_defs.iter().enumerate() {
                let child = a.values()[fi].as_ref();
                pairs.push((fd.name.clone(), array_value(&fd.data_type, child, i)?));
            }
            Ok(Value::Struct(pairs))
        }
    }
}

fn downcast_err(want: &str) -> SetOpError {
    SetOpError::compute(
        "arrow_downcast",
        format!("column is not the expected Arrow array: {want}"),
    )
}

/// Typed batch -> rows.
pub fn chunk_to_rows(schema: &Schema, chunk: &Chunk<Box<dyn Array>>) -> Result<Vec<Vec<Value>>> {
    let nrows = chunk.len();
    let mut rows = Vec::with_capacity(nrows);
    for i in 0..nrows {
        let mut row = Vec::with_capacity(schema.fields.len());
        for (f, arr) in schema.fields.iter().zip(chunk.columns()) {
            row.push(array_value(&f.data_type, arr.as_ref(), i)?);
        }
        rows.push(row);
    }
    Ok(rows)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::value::{json_to_value, parse_row};

    fn schema() -> Schema {
        serde_json::from_value::<Schema>(serde_json::json!({
            "fields": [
                {"name": "id", "data_type": {"kind": "int64"}, "nullable": true},
                {
                    "name": "tags",
                    "nullable": true,
                    "data_type": {"kind": "list", "name": "t",
                        "data_type": {"kind": "utf8"}, "nullable": true}
                },
                {
                    "name": "meta",
                    "nullable": true,
                    "data_type": {"kind": "struct", "fields": [
                        {"name": "flag", "data_type": {"kind": "bool"}, "nullable": true}
                    ]}
                }
            ]
        }))
        .unwrap()
    }

    #[test]
    fn nested_roundtrip_preserves_nulls_and_values() {
        let s = schema();
        let raw = [
            serde_json::json!([1, ["a", null, ""], {"flag": true}]),
            serde_json::json!([null, null, null]),
            serde_json::json!([3, [], {"flag": null}]),
        ];
        let rows: Vec<Vec<Value>> = raw.iter().map(|r| parse_row(&s, r).unwrap()).collect();
        let chunk = rows_to_chunk(&s, &rows).unwrap();
        assert_eq!(chunk.len(), 3);
        let back = chunk_to_rows(&s, &chunk).unwrap();
        assert_eq!(back, rows);
        // Explicit Arrow null checks.
        assert!(chunk.columns()[0].is_null(1));
        assert!(!chunk.columns()[0].is_null(0));
    }

    #[test]
    fn detects_type_mismatch() {
        let s = schema();
        let bad = serde_json::json!(["not-an-int", ["x"], {"flag": false}]);
        assert!(parse_row(&s, &bad).is_err());
    }

    #[test]
    fn empty_batch_is_valid() {
        let s = schema();
        let chunk = rows_to_chunk(&s, &[]).unwrap();
        assert_eq!(chunk.len(), 0);
        assert_eq!(chunk_to_rows(&s, &chunk).unwrap().len(), 0);
    }

    #[test]
    fn large_offsets_are_i64() {
        let ty = DataType::Utf8;
        assert_eq!(to_arrow_type(&ty), ADataType::LargeUtf8);
        let _ = json_to_value; // keep import used under all feature combos
    }
}
