//! Typed columnar batches backed by Arrow2 arrays.
//!
//! A [`Batch`] owns a small schema and a set of nullable columns.  Only the
//! physical types needed by the aggregation operators are modelled: signed
//! 64-bit integers (discrete measurements), 64-bit floats (continuous
//! measurements) and UTF-8 strings (group keys, mode input, ordered-string
//! input).  Everything is nullable; null-ness comes from the Arrow2 validity
//! bitmap rather than a sentinel value.

use std::sync::Arc;

use arrow2::array::{Array, MutableUtf8Array, PrimitiveArray, Utf8Array};
use arrow2::bitmap::Bitmap;
use arrow2::buffer::Buffer;
use serde_json::Value;

use crate::error::{Error, ErrorKind, Result};

/// Logical data type understood by the engine.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DataType {
    Int64,
    Float64,
    Utf8,
}

impl DataType {
    pub fn as_str(self) -> &'static str {
        match self {
            DataType::Int64 => "int64",
            DataType::Float64 => "float64",
            DataType::Utf8 => "utf8",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "int64" | "INT64" => Some(DataType::Int64),
            "float64" | "FLOAT64" | "double" => Some(DataType::Float64),
            "utf8" | "UTF8" | "string" => Some(DataType::Utf8),
            _ => None,
        }
    }
}

/// A single nullable column.
#[derive(Debug, Clone)]
pub enum Column {
    Int64(PrimitiveArray<i64>),
    Float64(PrimitiveArray<f64>),
    Utf8(Utf8Array<i32>),
}

impl Column {
    pub fn data_type(&self) -> DataType {
        match self {
            Column::Int64(_) => DataType::Int64,
            Column::Float64(_) => DataType::Float64,
            Column::Utf8(_) => DataType::Utf8,
        }
    }

    pub fn len(&self) -> usize {
        match self {
            Column::Int64(a) => a.len(),
            Column::Float64(a) => a.len(),
            Column::Utf8(a) => a.len(),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    pub fn is_null(&self, row: usize) -> bool {
        match self {
            Column::Int64(a) => a.validity().map(|v| !v.get_bit(row)).unwrap_or(false),
            Column::Float64(a) => a.validity().map(|v| !v.get_bit(row)).unwrap_or(false),
            Column::Utf8(a) => a.validity().map(|v| !v.get_bit(row)).unwrap_or(false),
        }
    }

    pub fn i64_at(&self, row: usize) -> Option<i64> {
        match self {
            Column::Int64(a) => a.get(row),
            _ => None,
        }
    }

    pub fn f64_at(&self, row: usize) -> Option<f64> {
        match self {
            Column::Float64(a) => a.get(row),
            _ => None,
        }
    }

    pub fn utf8_at(&self, row: usize) -> Option<&str> {
        match self {
            Column::Utf8(a) => a.get(row),
            _ => None,
        }
    }

    /// Build a nullable int64 column.
    pub fn from_i64(values: Vec<Option<i64>>) -> Self {
        let validity = build_validity(values.len(), |i| values[i].is_some());
        let buffer: Buffer<i64> = values.into_iter().map(|v| v.unwrap_or_default()).collect();
        Column::Int64(PrimitiveArray::new(
            arrow2::datatypes::DataType::Int64,
            buffer,
            validity,
        ))
    }

    /// Build a nullable float64 column.
    pub fn from_f64(values: Vec<Option<f64>>) -> Self {
        let validity = build_validity(values.len(), |i| values[i].is_some());
        let buffer: Buffer<f64> = values.into_iter().map(|v| v.unwrap_or_default()).collect();
        Column::Float64(PrimitiveArray::new(
            arrow2::datatypes::DataType::Float64,
            buffer,
            validity,
        ))
    }

    /// Build a nullable utf8 column.
    pub fn from_utf8(values: Vec<Option<String>>) -> Self {
        let mut builder = MutableUtf8Array::<i32>::with_capacity(values.len());
        for v in &values {
            builder.push(v.as_deref());
        }
        Column::Utf8(builder.into())
    }

    /// Parse a column from JSON values according to a declared physical type.
    pub fn from_json(values: &[Value], ty: DataType) -> Result<Self> {
        match ty {
            DataType::Int64 => {
                let parsed: Result<Vec<Option<i64>>> = values
                    .iter()
                    .map(|v| {
                        json_to_optional(v, |j| {
                            j.as_i64()
                                .or_else(|| {
                                    j.as_f64().and_then(|f| {
                                        if f.fract() == 0.0 {
                                            Some(f as i64)
                                        } else {
                                            None
                                        }
                                    })
                                })
                                .ok_or_else(|| {
                                    Error::new(
                                        ErrorKind::ParseError,
                                        format!("value {j} is not an int64"),
                                    )
                                })
                        })
                    })
                    .collect();
                Ok(Column::from_i64(parsed?))
            }
            DataType::Float64 => {
                let parsed: Result<Vec<Option<f64>>> = values
                    .iter()
                    .map(|v| {
                        json_to_optional(v, |j| {
                            j.as_f64().ok_or_else(|| {
                                Error::new(
                                    ErrorKind::ParseError,
                                    format!("value {j} is not a float64"),
                                )
                            })
                        })
                    })
                    .collect();
                Ok(Column::from_f64(parsed?))
            }
            DataType::Utf8 => {
                let parsed: Result<Vec<Option<String>>> = values
                    .iter()
                    .map(|v| {
                        json_to_optional(v, |j| match j {
                            Value::String(s) => Ok(s.clone()),
                            other => Err(Error::new(
                                ErrorKind::ParseError,
                                format!("value {other} is not a string"),
                            )),
                        })
                    })
                    .collect();
                Ok(Column::from_utf8(parsed?))
            }
        }
    }

    pub fn as_arrow_array(&self) -> Arc<dyn Array> {
        match self {
            Column::Int64(a) => Arc::new(a.clone()),
            Column::Float64(a) => Arc::new(a.clone()),
            Column::Utf8(a) => Arc::new(a.clone()),
        }
    }
}

fn json_to_optional<T>(v: &Value, parse: impl FnOnce(&Value) -> Result<T>) -> Result<Option<T>> {
    match v {
        Value::Null => Ok(None),
        other => parse(other).map(Some),
    }
}

fn build_validity(len: usize, is_valid: impl Fn(usize) -> bool) -> Option<Bitmap> {
    if (0..len).all(&is_valid) {
        return None;
    }
    let mut builder = arrow2::bitmap::MutableBitmap::with_capacity(len);
    for i in 0..len {
        builder.push(is_valid(i));
    }
    Some(Bitmap::from(builder))
}

/// A named, typed column declaration.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Field {
    pub name: String,
    pub data_type: DataType,
}

impl Field {
    pub fn new(name: impl Into<String>, data_type: DataType) -> Self {
        Self {
            name: name.into(),
            data_type,
        }
    }
}

/// A columnar batch: schema plus equal-length columns.
#[derive(Debug, Clone)]
pub struct Batch {
    fields: Vec<Field>,
    columns: Vec<Column>,
    row_count: usize,
}

impl Batch {
    pub fn new(fields: Vec<Field>, columns: Vec<Column>) -> Result<Self> {
        if fields.len() != columns.len() {
            return Err(Error::invalid_request(format!(
                "schema declares {} fields but {} columns were supplied",
                fields.len(),
                columns.len()
            )));
        }
        for (field, col) in fields.iter().zip(columns.iter()) {
            if field.data_type != col.data_type() {
                return Err(Error::invalid_request(format!(
                    "column '{}' declared as {} but supplied as {}",
                    field.name,
                    field.data_type.as_str(),
                    col.data_type().as_str()
                )));
            }
        }
        let row_count = columns.first().map(|c| c.len()).unwrap_or(0);
        if columns.iter().any(|c| c.len() != row_count) {
            return Err(Error::invalid_request("columns have unequal lengths"));
        }
        Ok(Self {
            fields,
            columns,
            row_count,
        })
    }

    pub fn fields(&self) -> &[Field] {
        &self.fields
    }

    pub fn columns(&self) -> &[Column] {
        &self.columns
    }

    pub fn row_count(&self) -> usize {
        self.row_count
    }

    pub fn column_index(&self, name: &str) -> Option<usize> {
        self.fields.iter().position(|f| f.name == name)
    }

    pub fn column(&self, name: &str) -> Option<&Column> {
        self.column_index(name).map(|i| &self.columns[i])
    }

    /// Parse a batch from a JSON columnar payload:
    /// `{"schema":[{"name":"g","data_type":"utf8"}, ...],
    ///   "columns":{"g":[...], ...}}`.
    pub fn from_json(schema: &[Field], columns: &serde_json::Map<String, Value>) -> Result<Self> {
        let n = schema
            .first()
            .and_then(|f| columns.get(&f.name))
            .and_then(|v| v.as_array())
            .map(|a| a.len())
            .unwrap_or(0);
        let mut built = Vec::with_capacity(schema.len());
        for field in schema {
            let values = columns.get(&field.name).ok_or_else(|| {
                Error::invalid_request(format!("missing column '{}'", field.name))
            })?;
            let arr = values.as_array().ok_or_else(|| {
                Error::invalid_request(format!("column '{}' must be a JSON array", field.name))
            })?;
            if arr.len() != n {
                return Err(Error::invalid_request(format!(
                    "column '{}' has {} rows, expected {n}",
                    field.name,
                    arr.len()
                )));
            }
            built.push(Column::from_json(arr, field.data_type)?);
        }
        Batch::new(schema.to_vec(), built)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nullable_int_column_round_trips() {
        let col = Column::from_i64(vec![Some(1), None, Some(3)]);
        assert_eq!(col.len(), 3);
        assert!(col.is_null(1));
        assert!(!col.is_null(0));
        assert_eq!(col.i64_at(2), Some(3));
        assert_eq!(col.i64_at(1), None);
    }

    #[test]
    fn nullable_utf8_column_round_trips() {
        let col = Column::from_utf8(vec![Some("a".to_string()), None, Some("c".to_string())]);
        assert!(col.is_null(1));
        assert_eq!(col.utf8_at(2), Some("c"));
        assert_eq!(col.utf8_at(1), None);
    }

    #[test]
    fn batch_rejects_type_mismatch() {
        let err = Batch::new(
            vec![Field::new("x", DataType::Int64)],
            vec![Column::from_f64(vec![Some(1.0)])],
        )
        .unwrap_err();
        assert_eq!(err.kind, ErrorKind::InvalidRequest);
    }

    #[test]
    fn batch_rejects_unequal_lengths() {
        let err = Batch::new(
            vec![
                Field::new("a", DataType::Int64),
                Field::new("b", DataType::Int64),
            ],
            vec![
                Column::from_i64(vec![Some(1)]),
                Column::from_i64(vec![Some(1), Some(2)]),
            ],
        )
        .unwrap_err();
        assert_eq!(err.kind, ErrorKind::InvalidRequest);
    }

    #[test]
    fn parses_json_payload() {
        let schema = vec![
            Field::new("g", DataType::Utf8),
            Field::new("v", DataType::Float64),
        ];
        let cols = serde_json::json!({
            "g": ["x", "y"],
            "v": [1.5, null],
        });
        let batch = Batch::from_json(&schema, cols.as_object().unwrap()).unwrap();
        assert_eq!(batch.row_count(), 2);
        assert!(batch.column("v").unwrap().is_null(1));
    }

    #[test]
    fn rejects_non_numeric_in_int_column() {
        let err = Column::from_json(&[serde_json::json!("oops")], DataType::Int64).unwrap_err();
        assert_eq!(err.kind, ErrorKind::ParseError);
    }
}
