//! Typed scalar value model.
//!
//! This is the *only* place where wire JSON is trusted. Every conversion is
//! checked against the declared [`ColumnType`](crate::plan::ColumnType); an
//! unexpected JSON shape produces an `invalid_data` error instead of a panic.

use std::hash::{Hash, Hasher};

use serde_json::Value;

use crate::error::{EngineError, EngineResult};
use crate::plan::ColumnType;

/// A runtime scalar.
///
/// Lists are kept as owned vectors so rows can be cheaply cloned between the
/// working table and the materialized result.
#[derive(Debug, Clone, PartialEq)]
pub enum Scalar {
    Int(i64),
    Utf8(String),
    Bool(bool),
    IntList(Vec<i64>),
    Utf8List(Vec<String>),
    /// Explicit SQL/JSON null.
    Null,
}

impl Scalar {
    /// Parse a JSON value as the declared column type.
    pub fn from_json(value: &Value, ty: ColumnType) -> EngineResult<Self> {
        match (ty, value) {
            (_, Value::Null) => Ok(Scalar::Null),
            (ColumnType::Int64, Value::Number(n)) => n
                .as_i64()
                .map(Scalar::Int)
                .ok_or_else(|| type_error(ty, value)),
            (ColumnType::Utf8, Value::String(s)) => Ok(Scalar::Utf8(s.clone())),
            (ColumnType::Bool, Value::Bool(b)) => Ok(Scalar::Bool(*b)),
            (ColumnType::ListInt64, Value::Array(items)) => {
                let mut out = Vec::with_capacity(items.len());
                for item in items {
                    match item {
                        Value::Number(n) => {
                            out.push(n.as_i64().ok_or_else(|| type_error(ty, value))?)
                        }
                        other => {
                            return Err(EngineError::invalid_data(format!(
                                "list<int64> element must be an integer, got {other}"
                            )))
                        }
                    }
                }
                Ok(Scalar::IntList(out))
            }
            (ColumnType::ListUtf8, Value::Array(items)) => {
                let mut out = Vec::with_capacity(items.len());
                for item in items {
                    match item {
                        Value::String(s) => out.push(s.clone()),
                        other => {
                            return Err(EngineError::invalid_data(format!(
                                "list<utf8> element must be a string, got {other}"
                            )))
                        }
                    }
                }
                Ok(Scalar::Utf8List(out))
            }
            _ => Err(type_error(ty, value)),
        }
    }

    /// Serialize back to wire JSON.
    pub fn to_json(&self) -> Value {
        match self {
            Scalar::Int(i) => Value::from(*i),
            Scalar::Utf8(s) => Value::from(s.clone()),
            Scalar::Bool(b) => Value::from(*b),
            Scalar::IntList(xs) => Value::Array(xs.iter().map(|i| Value::from(*i)).collect()),
            Scalar::Utf8List(xs) => {
                Value::Array(xs.iter().map(|s| Value::String(s.clone())).collect())
            }
            Scalar::Null => Value::Null,
        }
    }

    /// Type of this scalar. `Null` is compatible with every column type.
    pub fn runtime_type(&self) -> Option<ColumnType> {
        match self {
            Scalar::Null => None,
            Scalar::Int(_) => Some(ColumnType::Int64),
            Scalar::Utf8(_) => Some(ColumnType::Utf8),
            Scalar::Bool(_) => Some(ColumnType::Bool),
            Scalar::IntList(_) => Some(ColumnType::ListInt64),
            Scalar::Utf8List(_) => Some(ColumnType::ListUtf8),
        }
    }

    /// Key projection used for both set-dedup (`UNION`) and path membership.
    /// Only `Int`/`Utf8` keys are valid (enforced at validation).
    pub fn as_key(&self) -> Option<KeyRef<'_>> {
        match self {
            Scalar::Int(i) => Some(KeyRef::Int(*i)),
            Scalar::Utf8(s) => Some(KeyRef::Utf8(s.as_str())),
            _ => None,
        }
    }

    /// Feeds the *declared-key* portion of a row into a hasher. Never includes
    /// path/cycle columns: including them would make the fingerprint grow
    /// forever and defeat cycle detection.
    pub fn hash_key(&self, state: &mut impl Hasher) {
        match self {
            Scalar::Int(i) => {
                0u8.hash(state);
                i.hash(state);
            }
            Scalar::Utf8(s) => {
                1u8.hash(state);
                s.hash(state);
            }
            other => {
                // Unreachable for validated key columns; discriminant keeps
                // variants collision-free if validation is ever relaxed.
                255u8.hash(state);
                format!("{other:?}").hash(state);
            }
        }
    }

    /// Feeds the full scalar value into a hasher, for whole-row dedup.
    /// Every variant carries a distinct tag so unequal values cannot collide.
    pub fn fingerprint_component(&self, state: &mut impl Hasher) {
        match self {
            Scalar::Null => 0u8.hash(state),
            Scalar::Int(i) => {
                1u8.hash(state);
                i.hash(state);
            }
            Scalar::Utf8(s) => {
                2u8.hash(state);
                s.hash(state);
            }
            Scalar::Bool(b) => {
                3u8.hash(state);
                b.hash(state);
            }
            Scalar::IntList(xs) => {
                4u8.hash(state);
                xs.hash(state);
            }
            Scalar::Utf8List(xs) => {
                5u8.hash(state);
                xs.hash(state);
            }
        }
    }
}

/// Borrowed, hashable view of a declared-key value.
#[derive(Debug, PartialEq, Eq, Hash)]
pub enum KeyRef<'a> {
    Int(i64),
    Utf8(&'a str),
}

impl KeyRef<'_> {
    pub fn to_owned_scalar(&self) -> Scalar {
        match *self {
            KeyRef::Int(i) => Scalar::Int(i),
            KeyRef::Utf8(s) => Scalar::Utf8(s.to_string()),
        }
    }
}

fn type_error(ty: ColumnType, value: &Value) -> EngineError {
    EngineError::invalid_data(format!(
        "expected value of type {} but got JSON {}",
        ty.as_str(),
        value
    ))
}
