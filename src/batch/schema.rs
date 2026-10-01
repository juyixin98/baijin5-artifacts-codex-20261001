//! Logical schema shared by both inputs of a set operator.
//!
//! Typed fixture CSV headers look like `id:bigint,name:text,active:bool`.
//! Type spelling is part of the data contract and is validated before any
//! row is read, so a bad type is reported as an *input* error, not a parse
//! failure half-way through a file.

use arrow2::datatypes::{DataType, Field};

use crate::error::{InputCode, SetOpsError};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LogicalType {
    BigInt,
    Double,
    Boolean,
    Text,
}

impl LogicalType {
    pub fn parse(name: &str) -> Result<Self, SetOpsError> {
        match name.trim().to_ascii_lowercase().as_str() {
            "bigint" | "int8" | "long" => Ok(Self::BigInt),
            "double" | "float8" => Ok(Self::Double),
            "bool" | "boolean" => Ok(Self::Boolean),
            "text" | "string" | "varchar" => Ok(Self::Text),
            other => Err(SetOpsError::input(
                InputCode::UnknownType,
                format!("unknown column type '{other}' (supported: bigint, double, boolean, text)"),
            )),
        }
    }

    pub fn arrow_type(self) -> DataType {
        match self {
            Self::BigInt => DataType::Int64,
            Self::Double => DataType::Float64,
            Self::Boolean => DataType::Boolean,
            Self::Text => DataType::Utf8,
        }
    }

    /// Stable on-wire discriminator embedded in every non-null encoded value.
    pub fn type_tag(self) -> u8 {
        match self {
            Self::BigInt => 1,
            Self::Double => 2,
            Self::Boolean => 3,
            Self::Text => 4,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SchemaField {
    pub name: String,
    pub ty: LogicalType,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Schema {
    fields: Vec<SchemaField>,
}

impl Schema {
    pub fn new(fields: Vec<SchemaField>) -> Result<Self, SetOpsError> {
        if fields.is_empty() {
            return Err(SetOpsError::input(
                InputCode::InvalidRequest,
                "schema must contain at least one column",
            ));
        }
        Ok(Self { fields })
    }

    /// Parse a typed header row, e.g. `id:bigint,label:text`.
    pub fn parse_header(header: &str) -> Result<Self, SetOpsError> {
        let mut fields = Vec::new();
        for (idx, raw) in header.split(',').enumerate() {
            let part = raw.trim();
            let (name, tyraw) = part.split_once(':').ok_or_else(|| {
                SetOpsError::input(
                    InputCode::UnknownType,
                    format!("column {} '{part}' is missing a ':type' suffix", idx + 1),
                )
            })?;
            let name = name.trim();
            if name.is_empty() {
                return Err(SetOpsError::input(
                    InputCode::InvalidRequest,
                    format!("column {} has an empty name", idx + 1),
                ));
            }
            fields.push(SchemaField {
                name: name.to_string(),
                ty: LogicalType::parse(tyraw)?,
            });
        }
        Self::new(fields)
    }

    pub fn len(&self) -> usize {
        self.fields.len()
    }

    pub fn is_empty(&self) -> bool {
        self.fields.is_empty()
    }

    pub fn fields(&self) -> &[SchemaField] {
        &self.fields
    }

    pub fn field(&self, idx: usize) -> &SchemaField {
        &self.fields[idx]
    }

    pub fn arrow_fields(&self) -> Vec<Field> {
        self.fields
            .iter()
            .map(|f| Field::new(f.name.clone(), f.ty.arrow_type(), true))
            .collect()
    }

    /// Set operations require equal arity and pairwise equal data types.
    /// Column names may differ (SQL positional semantics).
    pub fn compatible_with(&self, other: &Schema) -> Result<(), SetOpsError> {
        if self.len() != other.len() {
            return Err(SetOpsError::input(
                InputCode::SchemaMismatch,
                format!(
                    "input arity differs: left has {} columns, right has {}",
                    self.len(),
                    other.len()
                ),
            ));
        }
        for (i, (a, b)) in self.fields.iter().zip(other.fields.iter()).enumerate() {
            if a.ty != b.ty {
                return Err(SetOpsError::input(
                    InputCode::SchemaMismatch,
                    format!(
                        "column {} type differs: left '{}' is {:?}, right '{}' is {:?}",
                        i + 1,
                        a.name,
                        a.ty,
                        b.name,
                        b.ty
                    ),
                ));
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_typed_header() {
        let s = Schema::parse_header(" id:bigint , label:text").unwrap();
        assert_eq!(s.len(), 2);
        assert_eq!(s.field(0).name, "id");
        assert_eq!(s.field(1).ty, LogicalType::Text);
    }

    #[test]
    fn rejects_missing_type_and_unknown_type() {
        assert!(matches!(
            Schema::parse_header("id").unwrap_err().kind,
            crate::error::ErrorKind::Input(InputCode::UnknownType)
        ));
        assert!(matches!(
            Schema::parse_header("id:json").unwrap_err().kind,
            crate::error::ErrorKind::Input(InputCode::UnknownType)
        ));
    }

    #[test]
    fn compatibility_is_positional_by_type() {
        let a = Schema::parse_header("a:bigint,b:text").unwrap();
        let b = Schema::parse_header("x:bigint,y:text").unwrap();
        let c = Schema::parse_header("a:bigint,b:bigint").unwrap();
        assert!(a.compatible_with(&b).is_ok());
        assert!(a.compatible_with(&c).is_err());
        assert!(
            a.compatible_with(&Schema::parse_header("a:bigint").unwrap())
                .is_err()
        );
    }
}
