//! Logical column types supported by the ordering engine.
//!
//! The supported type set is deliberately small but real: every type maps to a
//! concrete arrow2 array and has a defined total order. The engine is not a
//! hard-coded demo over one column type.

use arrow2::datatypes::{DataType, Field, Schema as ArrowSchema};
use serde::{Deserialize, Serialize};

use crate::error::{Result, SlError};

/// A comparable, nullable scalar type.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ColumnType {
    /// 64-bit signed integer.
    Int64,
    /// 64-bit IEEE float, compared under *total order* (NaN has a fixed place).
    Float64,
    /// UTF-8 string, compared by Rust string ordering (Unicode scalar value
    /// order; the engine documents this rather than claiming collation).
    Utf8,
    /// Boolean. Order: `false < true`.
    Boolean,
}

impl ColumnType {
    /// Map to the arrow2 physical/logical data type.
    pub fn to_arrow(self) -> DataType {
        match self {
            ColumnType::Int64 => DataType::Int64,
            ColumnType::Float64 => DataType::Float64,
            ColumnType::Utf8 => DataType::Utf8,
            ColumnType::Boolean => DataType::Boolean,
        }
    }

    /// Inverse of [`ColumnType::to_arrow`], restricted to the supported set.
    pub fn from_arrow(dt: &DataType) -> Result<Self> {
        match dt {
            DataType::Int64 => Ok(ColumnType::Int64),
            DataType::Float64 => Ok(ColumnType::Float64),
            DataType::Utf8 => Ok(ColumnType::Utf8),
            DataType::Boolean => Ok(ColumnType::Boolean),
            other => Err(SlError::validation(
                "unsupported_type",
                format!("column type {other:?} is not supported by the ordering engine"),
            )),
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            ColumnType::Int64 => "int64",
            ColumnType::Float64 => "float64",
            ColumnType::Utf8 => "utf8",
            ColumnType::Boolean => "boolean",
        }
    }
}

/// One named, typed column of a relation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ColumnSchema {
    pub name: String,
    pub ty: ColumnType,
    /// True when the column is nullable in the source. Nulls can still appear
    /// only when this is set; the comparator handles either way defensively.
    #[serde(default = "default_true")]
    pub nullable: bool,
}

fn default_true() -> bool {
    true
}

impl ColumnSchema {
    pub fn new(name: impl Into<String>, ty: ColumnType) -> Self {
        Self {
            name: name.into(),
            ty,
            nullable: true,
        }
    }

    pub fn to_arrow_field(&self) -> Field {
        Field::new(self.name.clone(), self.ty.to_arrow(), self.nullable)
    }
}

/// An ordered list of columns describing a relation/batch.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct RelationSchema {
    pub columns: Vec<ColumnSchema>,
}

impl RelationSchema {
    pub fn new(columns: Vec<ColumnSchema>) -> Self {
        Self { columns }
    }

    pub fn len(&self) -> usize {
        self.columns.len()
    }

    pub fn is_empty(&self) -> bool {
        self.columns.is_empty()
    }

    /// Resolve a column name to its position. Returns a categorized
    /// [`ErrorCategory::Validation`] error when the name is unknown.
    pub fn index_of(&self, name: &str) -> Result<usize> {
        self.columns
            .iter()
            .position(|c| c.name == name)
            .ok_or_else(|| {
                SlError::validation(
                    "unknown_column",
                    format!("no column named {name:?}; known columns: {:?}",
                        self.columns.iter().map(|c| c.name.as_str()).collect::<Vec<_>>()),
                )
            })
    }

    pub fn column(&self, idx: usize) -> &ColumnSchema {
        &self.columns[idx]
    }

    pub fn to_arrow_schema(&self) -> ArrowSchema {
        ArrowSchema::from(
            self.columns
                .iter()
                .map(ColumnSchema::to_arrow_field)
                .collect::<Vec<_>>(),
        )
    }
}
