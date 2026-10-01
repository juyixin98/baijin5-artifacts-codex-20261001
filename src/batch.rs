//! Typed columnar batches backed by Arrow2 arrays.
//!
//! The engine speaks three logical types only: `INTEGER` (i64), `TEXT` and
//! `BOOLEAN`. Each [`Column`] owns a concrete Arrow2 array; this is where the
//! Arrow2 dependency actually does work rather than decorative type names.

use arrow2::array::{Array, BooleanArray, PrimitiveArray, Utf8Array};
use arrow2::datatypes::{DataType as ArrowDataType, Field, Schema};
use serde_json::{Map, Value};
use std::sync::Arc;

use crate::error::{EngineError, EngineResult};

/// Logical data types supported by the service.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LogicalType {
    Integer,
    Text,
    Boolean,
}

impl LogicalType {
    pub fn name(&self) -> &'static str {
        match self {
            LogicalType::Integer => "INTEGER",
            LogicalType::Text => "TEXT",
            LogicalType::Boolean => "BOOLEAN",
        }
    }

    pub fn arrow_type(&self) -> ArrowDataType {
        match self {
            LogicalType::Integer => ArrowDataType::Int64,
            LogicalType::Text => ArrowDataType::Utf8,
            LogicalType::Boolean => ArrowDataType::Boolean,
        }
    }

    pub fn parse(name: &str) -> EngineResult<Self> {
        match name.trim().to_ascii_uppercase().as_str() {
            "INTEGER" | "INT" | "INT64" | "BIGINT" => Ok(LogicalType::Integer),
            "TEXT" | "VARCHAR" | "STRING" => Ok(LogicalType::Text),
            "BOOLEAN" | "BOOL" => Ok(LogicalType::Boolean),
            other => Err(EngineError::schema(format!("unknown type `{other}`"))),
        }
    }
}

/// A single nullable scalar value.
///
/// `NULL` is represented explicitly. Equality on [`ScalarValue`] is *identity*
/// equality (`Null == Null`), suitable for hash-group keys; SQL three-valued
/// comparison for joins uses [`sql_key_equal`] instead.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum ScalarValue {
    Null,
    Int(i64),
    Text(String),
    Bool(bool),
}

impl ScalarValue {
    pub fn logical_type(&self) -> Option<LogicalType> {
        match self {
            ScalarValue::Null => None,
            ScalarValue::Int(_) => Some(LogicalType::Integer),
            ScalarValue::Text(_) => Some(LogicalType::Text),
            ScalarValue::Bool(_) => Some(LogicalType::Boolean),
        }
    }

    pub fn to_json(&self) -> Value {
        match self {
            ScalarValue::Null => Value::Null,
            ScalarValue::Int(v) => Value::from(*v),
            ScalarValue::Text(v) => Value::from(v.clone()),
            ScalarValue::Bool(v) => Value::from(*v),
        }
    }
}

/// SQL equality for correlation keys: `NULL = anything` is UNKNOWN, i.e. not a
/// match. This is exactly the predicate used when probing a hash group.
pub fn sql_key_equal(a: &ScalarValue, b: &ScalarValue) -> bool {
    match (a, b) {
        (ScalarValue::Null, _) | (_, ScalarValue::Null) => false,
        (ScalarValue::Int(x), ScalarValue::Int(y)) => x == y,
        (ScalarValue::Text(x), ScalarValue::Text(y)) => x == y,
        (ScalarValue::Bool(x), ScalarValue::Bool(y)) => x == y,
        _ => false,
    }
}

/// A typed column wrapping a concrete Arrow2 array.
#[derive(Debug, Clone)]
pub enum Column {
    Integer {
        name: String,
        array: PrimitiveArray<i64>,
    },
    Text {
        name: String,
        array: Utf8Array<i32>,
    },
    Boolean {
        name: String,
        array: BooleanArray,
    },
}

impl Column {
    pub fn from_options(
        name: impl Into<String>,
        ty: LogicalType,
        raw: &[Value],
    ) -> EngineResult<Column> {
        let name = name.into();
        match ty {
            LogicalType::Integer => {
                let mut values = Vec::with_capacity(raw.len());
                for (i, v) in raw.iter().enumerate() {
                    values.push(match v {
                        Value::Null => None,
                        Value::Number(n) => Some(n.as_i64().ok_or_else(|| {
                            EngineError::validation(
                                format!("column `{name}` row {i}: integer value out of i64 range"),
                                "data",
                            )
                        })?),
                        other => {
                            return Err(EngineError::validation(
                                format!("column `{name}` row {i}: expected INTEGER, got {other}"),
                                "data",
                            ))
                        }
                    });
                }
                Ok(Column::Integer {
                    name,
                    array: PrimitiveArray::<i64>::from_iter(values),
                })
            }
            LogicalType::Text => {
                let mut values: Vec<Option<String>> = Vec::with_capacity(raw.len());
                for (i, v) in raw.iter().enumerate() {
                    match v {
                        Value::Null => values.push(None),
                        Value::String(s) => values.push(Some(s.clone())),
                        other => {
                            return Err(EngineError::validation(
                                format!("column `{name}` row {i}: expected TEXT, got {other}"),
                                "data",
                            ))
                        }
                    }
                }
                Ok(Column::Text {
                    name,
                    array: Utf8Array::<i32>::from_iter(values),
                })
            }
            LogicalType::Boolean => {
                let mut values: Vec<Option<bool>> = Vec::with_capacity(raw.len());
                for (i, v) in raw.iter().enumerate() {
                    match v {
                        Value::Null => values.push(None),
                        Value::Bool(b) => values.push(Some(*b)),
                        other => {
                            return Err(EngineError::validation(
                                format!("column `{name}` row {i}: expected BOOLEAN, got {other}"),
                                "data",
                            ))
                        }
                    }
                }
                Ok(Column::Boolean {
                    name,
                    array: BooleanArray::from_iter(values),
                })
            }
        }
    }

    /// Build a column of one logical type from a scalar vector (used to attach
    /// a computed EXISTS/aggregate column). Each scalar must match `ty` or be
    /// NULL.
    pub fn from_scalars(
        name: impl Into<String>,
        ty: LogicalType,
        scalars: Vec<ScalarValue>,
    ) -> EngineResult<Column> {
        let name = name.into();
        let raw: Vec<Value> = scalars
            .iter()
            .enumerate()
            .map(|(i, s)| match (s, ty) {
                (ScalarValue::Null, _) => Ok(Value::Null),
                (ScalarValue::Int(v), LogicalType::Integer) => Ok(Value::from(*v)),
                (ScalarValue::Text(v), LogicalType::Text) => Ok(Value::from(v.clone())),
                (ScalarValue::Bool(v), LogicalType::Boolean) => Ok(Value::from(*v)),
                (s, ty) => Err(EngineError::execution(format!(
                    "computed column `{name}` row {i}: value {s:?} does not fit {}",
                    ty.name()
                ))),
            })
            .collect::<EngineResult<Vec<_>>>()?;
        Column::from_options(name, ty, &raw)
    }

    pub fn name(&self) -> &str {
        match self {
            Column::Integer { name, .. }
            | Column::Text { name, .. }
            | Column::Boolean { name, .. } => name,
        }
    }

    pub fn logical_type(&self) -> LogicalType {
        match self {
            Column::Integer { .. } => LogicalType::Integer,
            Column::Text { .. } => LogicalType::Text,
            Column::Boolean { .. } => LogicalType::Boolean,
        }
    }

    pub fn len(&self) -> usize {
        match self {
            Column::Integer { array, .. } => array.len(),
            Column::Text { array, .. } => array.len(),
            Column::Boolean { array, .. } => array.len(),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    pub fn is_null(&self, row: usize) -> bool {
        match self {
            Column::Integer { array, .. } => array.is_null(row),
            Column::Text { array, .. } => array.is_null(row),
            Column::Boolean { array, .. } => array.is_null(row),
        }
    }

    pub fn value(&self, row: usize) -> ScalarValue {
        if self.is_null(row) {
            return ScalarValue::Null;
        }
        match self {
            Column::Integer { array, .. } => ScalarValue::Int(array.value(row)),
            Column::Text { array, .. } => ScalarValue::Text(array.value(row).to_string()),
            Column::Boolean { array, .. } => ScalarValue::Bool(array.value(row)),
        }
    }

    pub fn arrow_field(&self) -> Field {
        Field::new(self.name(), self.logical_type().arrow_type(), true)
    }

    pub fn arrow_array(&self) -> &dyn Array {
        match self {
            Column::Integer { array, .. } => array,
            Column::Text { array, .. } => array,
            Column::Boolean { array, .. } => array,
        }
    }
}

/// A columnar relation: ordered rows of named, typed columns.
#[derive(Debug, Clone)]
pub struct RecordBatch {
    columns: Vec<Column>,
    row_count: usize,
}

impl RecordBatch {
    pub fn new(columns: Vec<Column>) -> EngineResult<Self> {
        let row_count = columns.first().map(|c| c.len()).unwrap_or(0);
        for c in &columns {
            if c.len() != row_count {
                return Err(EngineError::execution(format!(
                    "ragged batch: column `{}` has {} rows, expected {row_count}",
                    c.name(),
                    c.len()
                )));
            }
        }
        Ok(RecordBatch { columns, row_count })
    }

    pub fn empty_from_schema(schema: &[(String, LogicalType)]) -> Self {
        let columns = schema
            .iter()
            .map(|(name, ty)| match ty {
                LogicalType::Integer => Column::Integer {
                    name: name.clone(),
                    array: PrimitiveArray::<i64>::from_iter(Vec::<Option<i64>>::new()),
                },
                LogicalType::Text => Column::Text {
                    name: name.clone(),
                    array: Utf8Array::<i32>::from_iter(Vec::<Option<String>>::new()),
                },
                LogicalType::Boolean => Column::Boolean {
                    name: name.clone(),
                    array: BooleanArray::from_iter(Vec::<Option<bool>>::new()),
                },
            })
            .collect();
        RecordBatch {
            columns,
            row_count: 0,
        }
    }

    pub fn row_count(&self) -> usize {
        self.row_count
    }

    pub fn columns(&self) -> &[Column] {
        &self.columns
    }

    pub fn arrow_schema(&self) -> Arc<Schema> {
        Arc::new(Schema::from(
            self.columns
                .iter()
                .map(|c| c.arrow_field())
                .collect::<Vec<_>>(),
        ))
    }

    pub fn column_index(&self, name: &str) -> EngineResult<usize> {
        self.columns
            .iter()
            .position(|c| c.name() == name)
            .ok_or_else(|| EngineError::schema(format!("column `{name}` not found")))
    }

    pub fn column(&self, name: &str) -> EngineResult<&Column> {
        self.columns
            .iter()
            .find(|c| c.name() == name)
            .ok_or_else(|| EngineError::schema(format!("column `{name}` not found")))
    }

    /// Values of one column as a vector of nullable scalars.
    pub fn column_values(&self, name: &str) -> EngineResult<Vec<ScalarValue>> {
        let col = self.column(name)?;
        Ok((0..self.row_count).map(|i| col.value(i)).collect())
    }

    /// Composite key for a row (used by hash grouping).
    pub fn row_key(&self, row: usize, key_indices: &[usize]) -> Vec<ScalarValue> {
        key_indices
            .iter()
            .map(|&ci| self.columns[ci].value(row))
            .collect()
    }

    /// Keep selected columns in the given order.
    pub fn project(&self, indices: &[usize]) -> EngineResult<RecordBatch> {
        let cols = indices.iter().map(|&i| self.columns[i].clone()).collect();
        RecordBatch::new(cols)
    }

    /// Append a computed column (used to attach boolean / aggregate results).
    pub fn with_column(&self, column: Column) -> EngineResult<RecordBatch> {
        if column.len() != self.row_count {
            return Err(EngineError::execution(format!(
                "appended column `{}` has {} rows, batch has {}",
                column.name(),
                column.len(),
                self.row_count
            )));
        }
        let mut columns = self.columns.clone();
        columns.push(column);
        Ok(RecordBatch {
            columns,
            row_count: self.row_count,
        })
    }

    /// Rows as JSON objects, in physical order (duplicates preserved).
    pub fn to_json_rows(&self) -> Vec<Value> {
        (0..self.row_count)
            .map(|row| {
                let mut map = Map::new();
                for col in &self.columns {
                    map.insert(col.name().to_string(), col.value(row).to_json());
                }
                Value::Object(map)
            })
            .collect()
    }
}
