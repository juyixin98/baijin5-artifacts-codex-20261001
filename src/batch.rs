//! Typed batches: wire JSON columns decoded into Arrow2 primitive/string arrays.
//!
//! All downstream operators consume [`TypedColumn`] rather than
//! `serde_json::Value`; scalar extraction and validation happen exactly once,
//! at the system boundary. Invalid cells fail fast with a field-level error.

use arrow2::array::{Array, Float64Array, Int64Array, Utf8Array};

use crate::error::{ErrorKind, PctlError, Result};
use crate::spec::{InputColumn, LogicalType};

/// One materialized, type-checked column backed by an Arrow2 array.
#[derive(Debug, Clone)]
pub enum TypedColumn {
    I64(Int64Array),
    F64(Float64Array),
    Utf8(Utf8Array<i32>),
}

impl TypedColumn {
    pub fn logical_type(&self) -> LogicalType {
        match self {
            TypedColumn::I64(_) => LogicalType::I64,
            TypedColumn::F64(_) => LogicalType::F64,
            TypedColumn::Utf8(_) => LogicalType::Utf8,
        }
    }

    pub fn len(&self) -> usize {
        match self {
            TypedColumn::I64(a) => a.len(),
            TypedColumn::F64(a) => a.len(),
            TypedColumn::Utf8(a) => a.len(),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

/// Typed scalar view used for dispatch from the ingest loop.
#[derive(Debug, Clone)]
pub enum Scalar<'a> {
    I64(i64),
    F64(f64),
    Utf8(&'a str),
}

// Convenience constructors used by tests that need values JSON cannot carry
// (NaN), delegating to Arrow2's own `FromIterator` arrays.
impl FromIterator<Option<i64>> for TypedColumn {
    fn from_iter<I: IntoIterator<Item = Option<i64>>>(it: I) -> Self {
        TypedColumn::I64(it.into_iter().collect())
    }
}
impl FromIterator<Option<f64>> for TypedColumn {
    fn from_iter<I: IntoIterator<Item = Option<f64>>>(it: I) -> Self {
        TypedColumn::F64(it.into_iter().collect())
    }
}
impl FromIterator<Option<String>> for TypedColumn {
    fn from_iter<I: IntoIterator<Item = Option<String>>>(it: I) -> Self {
        TypedColumn::Utf8(it.into_iter().collect())
    }
}

#[derive(Debug, Clone)]
pub struct TypedBatch {
    pub columns: Vec<TypedColumn>,
    pub len: usize,
}

impl TypedBatch {
    /// Decode every input column into Arrow2 arrays.
    pub fn from_input(columns: &[InputColumn]) -> Result<Self> {
        let mut out = Vec::with_capacity(columns.len());
        for (ci, col) in columns.iter().enumerate() {
            let typed = match col.data_type {
                LogicalType::I64 => {
                    let mut cells: Vec<Option<i64>> = Vec::with_capacity(col.values.len());
                    for v in &col.values {
                        match v {
                            serde_json::Value::Null => cells.push(None),
                            serde_json::Value::Number(n) => {
                                cells.push(Some(n.as_i64().ok_or_else(|| {
                                    cell_err(ci, "i64 cell must be an integer without fraction")
                                })?))
                            }
                            other => return Err(type_mismatch(ci, "integer or null", other)),
                        }
                    }
                    TypedColumn::I64(cells.into_iter().collect())
                }
                LogicalType::F64 => {
                    let mut cells: Vec<Option<f64>> = Vec::with_capacity(col.values.len());
                    for v in &col.values {
                        match v {
                            serde_json::Value::Null => cells.push(None),
                            serde_json::Value::Number(n) => {
                                cells.push(Some(n.as_f64().ok_or_else(|| {
                                    cell_err(ci, "f64 cell must be a JSON number")
                                })?))
                            }
                            other => return Err(type_mismatch(ci, "number or null", other)),
                        }
                    }
                    TypedColumn::F64(cells.into_iter().collect())
                }
                LogicalType::Utf8 => {
                    let mut cells: Vec<Option<&str>> = Vec::with_capacity(col.values.len());
                    for v in &col.values {
                        match v {
                            serde_json::Value::Null => cells.push(None),
                            serde_json::Value::String(s) => cells.push(Some(s.as_str())),
                            other => return Err(type_mismatch(ci, "string or null", other)),
                        }
                    }
                    TypedColumn::Utf8(cells.into_iter().collect())
                }
            };
            out.push(typed);
        }
        let len = out.first().map(|c| c.len()).unwrap_or(0);
        for (ci, c) in out.iter().enumerate() {
            if c.len() != len {
                return Err(PctlError::new(
                    ErrorKind::InvalidRequest,
                    "ragged_columns",
                    format!(
                        "column {} ({}) has {} rows but the first column has {len}",
                        ci,
                        columns[ci].name,
                        c.len()
                    ),
                )
                .at(format!("columns[{ci}].values")));
            }
        }
        Ok(TypedBatch { columns: out, len })
    }

    /// Extract one scalar without copying; NULL returns `None`.
    pub fn get(&self, column: usize, row: usize) -> Option<Scalar<'_>> {
        match &self.columns[column] {
            TypedColumn::I64(a) => {
                if a.is_null(row) {
                    None
                } else {
                    Some(Scalar::I64(a.get(row).unwrap()))
                }
            }
            TypedColumn::F64(a) => {
                if a.is_null(row) {
                    None
                } else {
                    Some(Scalar::F64(a.get(row).unwrap()))
                }
            }
            TypedColumn::Utf8(a) => {
                if a.is_null(row) {
                    None
                } else {
                    Some(Scalar::Utf8(a.get(row).unwrap()))
                }
            }
        }
    }
}

fn cell_err(ci: usize, msg: &str) -> PctlError {
    PctlError::new(
        ErrorKind::InvalidRequest,
        "cell_type_mismatch",
        format!("column {ci}: {msg}"),
    )
    .at(format!("columns[{ci}].values"))
}

fn type_mismatch(ci: usize, expected: &str, got: &serde_json::Value) -> PctlError {
    let actual = match got {
        serde_json::Value::Bool(_) => "boolean",
        serde_json::Value::Array(_) => "array",
        serde_json::Value::Object(_) => "object",
        _ => "other",
    };
    PctlError::new(
        ErrorKind::InvalidRequest,
        "cell_type_mismatch",
        format!("expected {expected}, got {actual}"),
    )
    .at(format!("columns[{ci}].values"))
}
