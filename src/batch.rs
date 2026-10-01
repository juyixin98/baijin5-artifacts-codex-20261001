//! Typed columnar batches.
//!
//! Columns are ingested as typed, null-aware Rust vectors and then converted to
//! Arrow2 arrays (`PrimitiveArray<i64>`, `Utf8Array<i32>`, `BooleanArray>`),
//! assembled into an Arrow2 [`Chunk`]. Nulls live in Arrow2 validity bitmaps —
//! the source of truth for IS NULL and for UNKNOWN in comparisons.

use arrow2::array::{Array, BooleanArray, PrimitiveArray, Utf8Array};
use arrow2::chunk::Chunk;
use arrow2::datatypes::{DataType, Field, Schema};
use serde::{Deserialize, Serialize};

use crate::error::{Error, ErrorKind, Result};

/// Supported logical column types.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ColumnType {
    Int,
    Text,
    Bool,
}

impl ColumnType {
    pub fn arrow_type(self) -> DataType {
        match self {
            ColumnType::Int => DataType::Int64,
            ColumnType::Text => DataType::Utf8,
            ColumnType::Bool => DataType::Boolean,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            ColumnType::Int => "int",
            ColumnType::Text => "text",
            ColumnType::Bool => "bool",
        }
    }
}

/// Typed, null-aware column data for one append batch.
#[derive(Debug, Clone, PartialEq)]
pub enum ColumnData {
    Int(Vec<Option<i64>>),
    Text(Vec<Option<String>>),
    Bool(Vec<Option<bool>>),
}

impl ColumnData {
    pub fn len(&self) -> usize {
        match self {
            ColumnData::Int(v) => v.len(),
            ColumnData::Text(v) => v.len(),
            ColumnData::Bool(v) => v.len(),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    pub fn col_type(&self) -> ColumnType {
        match self {
            ColumnData::Int(_) => ColumnType::Int,
            ColumnData::Text(_) => ColumnType::Text,
            ColumnData::Bool(_) => ColumnType::Bool,
        }
    }

    /// True where the value is NULL (same positions as Arrow2 validity=false).
    pub fn null_mask(&self) -> Vec<bool> {
        match self {
            ColumnData::Int(v) => v.iter().map(|x| x.is_none()).collect(),
            ColumnData::Text(v) => v.iter().map(|x| x.is_none()).collect(),
            ColumnData::Bool(v) => v.iter().map(|x| x.is_none()).collect(),
        }
    }

    /// Convert to an Arrow2 array. NULLs become the Arrow2 validity bitmap.
    pub fn to_arrow_array(&self) -> Box<dyn Array> {
        match self {
            ColumnData::Int(v) => {
                let arr: PrimitiveArray<i64> =
                    PrimitiveArray::from_iter(v.iter().copied()).to(DataType::Int64);
                Box::new(arr)
            }
            ColumnData::Text(v) => {
                // Utf8Array<O> from Option<&str> defaults to DataType::Utf8 for O=i32.
                let arr: Utf8Array<i32> = Utf8Array::from_iter(v.iter().map(|x| x.as_deref()));
                box_arr(arr)
            }
            ColumnData::Bool(v) => {
                let arr: BooleanArray = BooleanArray::from_iter(v.iter().copied());
                Box::new(arr)
            }
        }
    }

    pub fn append(&mut self, other: ColumnData) -> Result<()> {
        match (self, other) {
            (ColumnData::Int(a), ColumnData::Int(mut b)) => {
                a.append(&mut b);
                Ok(())
            }
            (ColumnData::Text(a), ColumnData::Text(mut b)) => {
                a.append(&mut b);
                Ok(())
            }
            (ColumnData::Bool(a), ColumnData::Bool(mut b)) => {
                a.append(&mut b);
                Ok(())
            }
            (a, b) => Err(Error::new(
                ErrorKind::SchemaMismatch,
                format!(
                    "cannot append {:?} column onto {:?} column",
                    b.col_type(),
                    a.col_type()
                ),
            )),
        }
    }
}

/// Small helper to keep turbofish noise down.
fn box_arr<T: Array + 'static>(a: T) -> Box<dyn Array> {
    Box::new(a)
}

/// One declared column.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ColumnSpec {
    pub name: String,
    #[serde(rename = "type")]
    pub col_type: ColumnType,
}

/// A typed batch: an ordered set of named columns all of equal row count.
#[derive(Debug, Clone)]
pub struct TypedBatch {
    pub columns: Vec<(ColumnSpec, ColumnData)>,
}

impl TypedBatch {
    /// Construct from ordered (spec, data) pairs, validating names, types and
    /// equal lengths.
    pub fn new(columns: Vec<(ColumnSpec, ColumnData)>) -> Result<Self> {
        if columns.is_empty() {
            return Err(Error::invalid("batch must contain at least one column"));
        }
        let rows = columns[0].1.len();
        for (spec, data) in &columns {
            if spec.col_type != data.col_type() {
                return Err(Error::new(
                    ErrorKind::SchemaMismatch,
                    format!(
                        "column '{}' declared {} but holds {} values",
                        spec.name,
                        spec.col_type.as_str(),
                        data.col_type().as_str()
                    ),
                ));
            }
            if data.len() != rows {
                return Err(Error::invalid(format!(
                    "column '{}' has {} rows, expected {}",
                    spec.name,
                    data.len(),
                    rows
                )));
            }
        }
        let mut seen = std::collections::HashSet::new();
        for (spec, _) in &columns {
            if !seen.insert(spec.name.as_str()) {
                return Err(Error::invalid(format!("duplicate column '{}'", spec.name)));
            }
        }
        Ok(Self { columns })
    }

    pub fn row_count(&self) -> usize {
        self.columns.first().map(|(_, d)| d.len()).unwrap_or(0)
    }

    pub fn spec(&self) -> Vec<ColumnSpec> {
        self.columns.iter().map(|(s, _)| s.clone()).collect()
    }

    pub fn column(&self, name: &str) -> Option<&(ColumnSpec, ColumnData)> {
        self.columns.iter().find(|(s, _)| s.name == name)
    }

    pub fn column_mut(&mut self, name: &str) -> Option<&mut (ColumnSpec, ColumnData)> {
        self.columns.iter_mut().find(|(s, _)| s.name == name)
    }

    /// Build an Arrow2 schema + chunk for the whole batch.
    pub fn to_arrow_chunk(&self) -> (Schema, Chunk<Box<dyn Array>>) {
        let fields: Vec<Field> = self
            .columns
            .iter()
            .map(|(s, _)| Field::new(s.name.clone(), s.col_type.arrow_type(), true))
            .collect();
        let arrays: Vec<Box<dyn Array>> = self
            .columns
            .iter()
            .map(|(_, d)| d.to_arrow_array())
            .collect();
        (
            Schema::from(fields),
            Chunk::try_new(arrays).expect("validated TypedBatch -> equal-length arrays"),
        )
    }

    /// Append another batch with a compatible spec.
    pub fn append(&mut self, other: TypedBatch) -> Result<()> {
        if self.spec() != other.spec() {
            return Err(Error::new(
                ErrorKind::SchemaMismatch,
                "append batch schema does not match existing table schema",
            ));
        }
        for (mine, theirs) in self.columns.iter_mut().zip(other.columns) {
            mine.1.append(theirs.1)?;
        }
        Ok(())
    }
}

/// JSON row shape used by the load API: `{"col": 1, "other": null}`.
pub fn typed_batch_from_json_rows(
    spec: &[ColumnSpec],
    rows: &[serde_json::Map<String, serde_json::Value>],
) -> Result<TypedBatch> {
    let mut cols: Vec<ColumnData> = spec
        .iter()
        .map(|s| match s.col_type {
            ColumnType::Int => ColumnData::Int(Vec::with_capacity(rows.len())),
            ColumnType::Text => ColumnData::Text(Vec::with_capacity(rows.len())),
            ColumnType::Bool => ColumnData::Bool(Vec::with_capacity(rows.len())),
        })
        .collect();

    for (row_idx, row) in rows.iter().enumerate() {
        for (ci, c) in spec.iter().enumerate() {
            let value = row.get(&c.name);
            push_json_value(&mut cols[ci], c, value, row_idx)?;
        }
        // Reject undeclared columns at the boundary.
        for key in row.keys() {
            if !spec.iter().any(|c| &c.name == key) {
                return Err(Error::invalid(format!(
                    "row {row_idx}: unknown column '{key}'"
                )));
            }
        }
    }
    let pairs = spec.iter().cloned().zip(cols).collect();
    TypedBatch::new(pairs)
}

fn push_json_value(
    col: &mut ColumnData,
    spec: &ColumnSpec,
    value: Option<&serde_json::Value>,
    row_idx: usize,
) -> Result<()> {
    let bad = |found: &str| {
        Error::new(
            ErrorKind::TypeMismatch,
            format!(
                "row {row_idx} column '{}': expected {}, got {found}",
                spec.name,
                spec.col_type.as_str()
            ),
        )
    };
    match (value, col) {
        (None | Some(serde_json::Value::Null), ColumnData::Int(v)) => v.push(None),
        (None | Some(serde_json::Value::Null), ColumnData::Text(v)) => v.push(None),
        (None | Some(serde_json::Value::Null), ColumnData::Bool(v)) => v.push(None),
        (Some(serde_json::Value::Number(n)), ColumnData::Int(v)) => {
            let i = n.as_i64().ok_or_else(|| bad("non-integer number"))?;
            v.push(Some(i));
        }
        (Some(serde_json::Value::String(s)), ColumnData::Text(v)) => v.push(Some(s.clone())),
        (Some(serde_json::Value::Bool(b)), ColumnData::Bool(v)) => v.push(Some(*b)),
        (Some(other), _) => return Err(bad(&other.to_string())),
    }
    Ok(())
}
