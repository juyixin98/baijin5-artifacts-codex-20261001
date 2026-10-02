//! Typed, schema-driven values and their JSON mapping.
//!
//! The engine never works on untyped JSON: incoming JSON is validated against a
//! [`Schema`] and converted into [`Value`] at the boundary. The type system is
//! intentionally small but nested (lists and structs), which is enough to
//! exercise column boundaries, type tagging and *nested NULLs* in the row
//! encoding.
//!
//! Set operations use SQL set-semantics for NULL: two NULLs of the same type
//! are considered the same value. Rust's derived `PartialEq` already gives
//! `Null == Null`, so no bespoke equality is needed here.

use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};

use crate::error::{Result, SetOpError};

/// A field of a struct, or the element description of a list.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Field {
    pub name: String,
    pub data_type: DataType,
    /// Accepted for schema fidelity. Set operations compare NULLs as equal, so
    /// NULLs are permitted regardless of this flag (documented behaviour).
    #[serde(default = "default_true")]
    pub nullable: bool,
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum DataType {
    Int64,
    Utf8,
    Bool,
    List(Box<Field>),
    Struct { fields: Vec<Field> },
}

impl DataType {
    pub fn name(&self) -> &'static str {
        match self {
            DataType::Int64 => "int64",
            DataType::Utf8 => "utf8",
            DataType::Bool => "bool",
            DataType::List(_) => "list",
            DataType::Struct { .. } => "struct",
        }
    }
}

/// An ordered list of named, typed columns. Both inputs of a set operation
/// must share an identical schema.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Schema {
    pub fields: Vec<Field>,
}

impl Schema {
    pub fn validate(&self) -> Result<()> {
        if self.fields.is_empty() {
            return Err(SetOpError::input(
                "empty_schema",
                "schema must declare at least one column",
            ));
        }
        let mut seen = BTreeSet::new();
        for f in &self.fields {
            validate_field(f)?;
            if !seen.insert(&f.name) {
                return Err(SetOpError::input(
                    "duplicate_field",
                    format!("duplicate column name: {}", f.name),
                ));
            }
        }
        Ok(())
    }
}

fn validate_field(f: &Field) -> Result<()> {
    if f.name.is_empty() {
        return Err(SetOpError::input(
            "empty_field_name",
            "field names must be non-empty",
        ));
    }
    match &f.data_type {
        DataType::Int64 | DataType::Utf8 | DataType::Bool => Ok(()),
        DataType::List(inner) => validate_field(inner),
        DataType::Struct { fields: fs } => {
            if fs.is_empty() {
                return Err(SetOpError::input(
                    "empty_struct",
                    "struct types must have >= 1 field",
                ));
            }
            let mut seen = BTreeSet::new();
            for cf in fs {
                validate_field(cf)?;
                if !seen.insert(&cf.name) {
                    return Err(SetOpError::input(
                        "duplicate_field",
                        format!("duplicate struct field name: {}", cf.name),
                    ));
                }
            }
            Ok(())
        }
    }
}

/// A runtime value. `Null` carries no type (NULL is NULL across the column),
/// matching SQL set-operation equality.
#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    Null,
    Bool(bool),
    Int64(i64),
    Utf8(String),
    List(Vec<Value>),
    /// Field name / value pairs in schema order.
    Struct(Vec<(String, Value)>),
}

impl Value {
    /// Rough resident-memory size of the value tree, used for memory budgets.
    pub fn mem_size(&self) -> usize {
        const NODE: usize = std::mem::size_of::<Value>();
        match self {
            Value::Null | Value::Bool(_) | Value::Int64(_) => NODE,
            Value::Utf8(s) => NODE + s.len(),
            Value::List(items) => NODE + items.iter().map(Value::mem_size).sum::<usize>(),
            Value::Struct(fields) => {
                NODE + fields
                    .iter()
                    .map(|(n, v)| n.len() + std::mem::size_of::<String>() + v.mem_size())
                    .sum::<usize>()
            }
        }
    }
}

/// Convert and validate a JSON value against an expected type.
pub fn json_to_value(ty: &DataType, jv: &serde_json::Value) -> Result<Value> {
    // NULL is legal for every type.
    if jv.is_null() {
        return Ok(Value::Null);
    }
    match (ty, jv) {
        (DataType::Int64, serde_json::Value::Number(n)) => match n.is_i64() {
            true => Ok(Value::Int64(n.as_i64().unwrap())),
            false => Err(SetOpError::input(
                "type_mismatch",
                format!("expected int64, got out-of-range/non-integral number: {n}"),
            )),
        },
        (DataType::Utf8, serde_json::Value::String(s)) => Ok(Value::Utf8(s.clone())),
        (DataType::Bool, serde_json::Value::Bool(b)) => Ok(Value::Bool(*b)),
        (DataType::List(inner), serde_json::Value::Array(arr)) => arr
            .iter()
            .map(|x| json_to_value(&inner.data_type, x))
            .collect::<Result<Vec<_>>>()
            .map(Value::List),
        (DataType::Struct { fields: field_defs }, serde_json::Value::Object(map)) => {
            let mut out = Vec::with_capacity(field_defs.len());
            for fd in field_defs.iter() {
                match map.get(&fd.name) {
                    Some(v) => out.push((fd.name.clone(), json_to_value(&fd.data_type, v)?)),
                    None => {
                        return Err(SetOpError::input(
                            "missing_field",
                            format!("struct value missing field: {}", fd.name),
                        ));
                    }
                }
            }
            for k in map.keys() {
                if !field_defs.iter().any(|fd| &fd.name == k) {
                    return Err(SetOpError::input(
                        "unknown_field",
                        format!("unexpected struct field: {k}"),
                    ));
                }
            }
            Ok(Value::Struct(out))
        }
        _ => Err(SetOpError::input(
            "type_mismatch",
            format!("expected {}, got JSON {}", ty.name(), json_type_name(jv)),
        )),
    }
}

fn json_type_name(jv: &serde_json::Value) -> &'static str {
    match jv {
        serde_json::Value::Null => "null",
        serde_json::Value::Bool(_) => "bool",
        serde_json::Value::Number(_) => "number",
        serde_json::Value::String(_) => "string",
        serde_json::Value::Array(_) => "array",
        serde_json::Value::Object(_) => "object",
    }
}

/// Convert a validated value back to JSON for results.
pub fn value_to_json(v: &Value) -> serde_json::Value {
    match v {
        Value::Null => serde_json::Value::Null,
        Value::Bool(b) => serde_json::Value::Bool(*b),
        Value::Int64(i) => serde_json::Value::from(*i),
        Value::Utf8(s) => serde_json::Value::String(s.clone()),
        Value::List(items) => serde_json::Value::Array(items.iter().map(value_to_json).collect()),
        Value::Struct(fields) => {
            let mut map = serde_json::Map::with_capacity(fields.len());
            for (k, v) in fields {
                map.insert(k.clone(), value_to_json(v));
            }
            serde_json::Value::Object(map)
        }
    }
}

/// Convert a whole row (ordered column values) into a JSON array.
pub fn value_to_json_row(row: &[Value]) -> serde_json::Value {
    serde_json::Value::Array(row.iter().map(value_to_json).collect())
}

/// Parse a JSON row (a JSON array of column values) against a schema.
pub fn parse_row(schema: &Schema, row: &serde_json::Value) -> Result<Vec<Value>> {
    let arr = row.as_array().ok_or_else(|| {
        SetOpError::input(
            "row_not_array",
            "each row must be a JSON array of column values",
        )
    })?;
    if arr.len() != schema.fields.len() {
        return Err(SetOpError::input(
            "row_arity",
            format!(
                "row has {} columns, schema expects {}",
                arr.len(),
                schema.fields.len()
            ),
        ));
    }
    schema
        .fields
        .iter()
        .zip(arr.iter())
        .map(|(f, jv)| json_to_value(&f.data_type, jv))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nested_null_roundtrips_and_type_tags_matter() {
        let ty = DataType::List(Box::new(Field {
            name: "x".into(),
            data_type: DataType::Utf8,
            nullable: true,
        }));
        let v = json_to_value(&ty, &serde_json::json!(["a", null, ""])).unwrap();
        match &v {
            Value::List(items) => {
                assert_eq!(items[1], Value::Null);
                assert_eq!(items[2], Value::Utf8(String::new()));
                assert_ne!(items[1], items[2]);
            }
            _ => panic!(),
        }
    }

    #[test]
    fn rejects_wrong_and_out_of_range_types() {
        assert!(json_to_value(&DataType::Int64, &serde_json::json!("1")).is_err());
        assert!(json_to_value(&DataType::Bool, &serde_json::json!(1)).is_err());
        // i64::MAX + 1 cannot be represented as i64.
        assert!(
            json_to_value(
                &DataType::Int64,
                &serde_json::json!(9_223_372_036_854_775_808i128)
            )
            .is_err()
        );
    }

    #[test]
    fn struct_requires_exact_field_set() {
        let ty = DataType::Struct {
            fields: vec![Field {
                name: "a".into(),
                data_type: DataType::Int64,
                nullable: true,
            }],
        };
        assert!(json_to_value(&ty, &serde_json::json!({"a": 1})).is_ok());
        assert!(json_to_value(&ty, &serde_json::json!({})).is_err());
        assert!(json_to_value(&ty, &serde_json::json!({"a":1,"b":2})).is_err());
    }
}
