//! Column type tags and batch schemas.

use crate::error::{EngineError, Result};

use super::value::Value;

/// Supported logical column data types.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum DataType {
    /// SQL `BIGINT`.
    Int64,
    /// SQL `VARCHAR` / `TEXT`.
    Utf8,
    /// SQL `BOOLEAN`.
    Boolean,
}

impl DataType {
    /// Stable lowercase name used in JSON and validation messages.
    pub fn as_str(self) -> &'static str {
        match self {
            DataType::Int64 => "int64",
            DataType::Utf8 => "utf8",
            DataType::Boolean => "boolean",
        }
    }

    /// Parse the JSON-facing type name (`"int64" | "utf8" | "boolean"`).
    pub fn parse(name: &str) -> Option<Self> {
        match name {
            "int64" => Some(DataType::Int64),
            "utf8" | "string" | "varchar" => Some(DataType::Utf8),
            "boolean" | "bool" => Some(DataType::Boolean),
            _ => None,
        }
    }

    fn discriminant(self) -> u8 {
        match self {
            DataType::Int64 => 0,
            DataType::Utf8 => 1,
            DataType::Boolean => 2,
        }
    }

    /// Type-discriminated order used in stable traversal comparisons.
    pub fn stable_order(self, other: DataType) -> std::cmp::Ordering {
        self.discriminant().cmp(&other.discriminant())
    }
}

/// An ordered, named column definition.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Field {
    /// Column name.
    pub name: String,
    /// Column type.
    pub data_type: DataType,
}

impl Field {
    /// Create a field.
    pub fn new(name: impl Into<String>, data_type: DataType) -> Self {
        Self {
            name: name.into(),
            data_type,
        }
    }
}

/// Ordered list of fields describing a [`super::RecordBatch`].
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Schema {
    fields: Vec<Field>,
}

impl Schema {
    /// Build a schema, rejecting duplicate or empty column names.
    pub fn new(fields: Vec<Field>) -> Result<Self> {
        if fields.is_empty() {
            return Err(EngineError::validation(
                "schema must contain at least one column",
            ));
        }
        for f in &fields {
            if f.name.is_empty() {
                return Err(EngineError::validation("column names must be non-empty"));
            }
        }
        for (i, a) in fields.iter().enumerate() {
            if fields.iter().skip(i + 1).any(|b| b.name == a.name) {
                return Err(EngineError::validation(format!(
                    "duplicate column name: {}",
                    a.name
                )));
            }
        }
        Ok(Schema { fields })
    }

    /// All fields in order.
    pub fn fields(&self) -> &[Field] {
        &self.fields
    }

    /// Number of columns.
    pub fn column_count(&self) -> usize {
        self.fields.len()
    }

    /// Number of rows a schema with this shape requires in every column.
    /// (Reserved for future use; kept explicit next to the schema definition.)
    #[allow(dead_code)]
    pub fn expected_row_width(&self) -> usize {
        self.fields.len()
    }

    /// Look up a column index by name.
    pub fn index_of(&self, name: &str) -> Option<usize> {
        self.fields.iter().position(|f| f.name == name)
    }

    /// Require a column to exist.
    pub fn require_index(&self, name: &str) -> Result<usize> {
        self.index_of(name).ok_or_else(|| {
            EngineError::validation(format!(
                "unknown column '{name}'; available columns: {}",
                self.fields
                    .iter()
                    .map(|f| f.name.as_str())
                    .collect::<Vec<_>>()
                    .join(", ")
            ))
        })
    }

    /// Ensure two schemas have identical column types (names may differ,
    /// matching SQL `UNION` column-position compatibility).
    pub fn assert_types_compatible(&self, other: &Schema, context: &str) -> Result<()> {
        if self.fields.len() != other.fields.len() {
            return Err(EngineError::validation(format!(
                "{context}: column count mismatch: {} vs {}",
                self.fields.len(),
                other.fields.len()
            )));
        }
        for (a, b) in self.fields.iter().zip(other.fields.iter()) {
            if a.data_type != b.data_type {
                return Err(EngineError::validation(format!(
                    "{context}: column '{}' ({}) is incompatible with '{}' ({})",
                    a.name,
                    a.data_type.as_str(),
                    b.name,
                    b.data_type.as_str()
                )));
            }
        }
        Ok(())
    }
}

/// Type-check a scalar against a declared column type.
///
/// `NULL` is legal for every type; anything else must match exactly — the
/// supported subset has no implicit coercions.
pub fn check_value(value: &Value, ty: DataType) -> Result<()> {
    let ok = match (value, ty) {
        (Value::Null, _) => true,
        (Value::Int64(_), DataType::Int64) => true,
        (Value::Utf8(_), DataType::Utf8) => true,
        (Value::Boolean(_), DataType::Boolean) => true,
        (v, ty) => {
            return Err(EngineError::validation(format!(
                "value {} has type {} but column expects {}",
                v,
                v.type_name(),
                ty.as_str()
            )))
        }
    };
    if ok {
        Ok(())
    } else {
        Err(EngineError::validation(format!(
            "type mismatch with column type {}",
            ty.as_str()
        )))
    }
}
