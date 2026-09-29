//! Convenient constructors for fixtures, tests and JSON decoding.

use std::sync::Arc;

use arrow2::array::{PrimitiveArray, Utf8Array};

use crate::error::{ErrorCode, JoinError, JoinResult};
use crate::types::batch::{Column, TypedBatch};
use crate::types::value::KeyType;

pub fn int_column(name: impl Into<String>, values: Vec<Option<i64>>) -> Column {
    let array = PrimitiveArray::<i64>::from(values).to(arrow2::datatypes::DataType::Int64);
    Column::new(name, KeyType::Int64, Arc::new(array)).expect("valid int column")
}

pub fn float_column(name: impl Into<String>, values: Vec<Option<f64>>) -> Column {
    let array = PrimitiveArray::<f64>::from(values).to(arrow2::datatypes::DataType::Float64);
    Column::new(name, KeyType::Float64, Arc::new(array)).expect("valid float column")
}

pub fn utf8_column(name: impl Into<String>, values: Vec<Option<&str>>) -> Column {
    let array: Utf8Array<i32> = values.into_iter().collect();
    Column::new(name, KeyType::Utf8, Arc::new(array)).expect("valid utf8 column")
}

pub fn batch(columns: Vec<Column>) -> JoinResult<TypedBatch> {
    TypedBatch::try_new(columns)
}

/// Build a single-column int batch; fails on purpose (returns error) if asked
/// to build an empty *schema* — zero rows are fine.
pub fn single_int_batch(col: &str, values: Vec<Option<i64>>) -> JoinResult<TypedBatch> {
    batch(vec![int_column(col, values)])
}

/// Fail fast in fixture code when a batch is structurally impossible.
pub fn expect_batch(columns: Vec<Column>) -> TypedBatch {
    batch(columns).unwrap_or_else(|e| panic!("fixture batch construction failed: {e:?}"))
}

/// Type-tag guard used by the JSON API: reject conflicting declared types.
pub fn ensure_declared(
    array_type: arrow2::datatypes::DataType,
    declared: KeyType,
) -> JoinResult<()> {
    let ok = match declared {
        KeyType::Int64 => array_type == arrow2::datatypes::DataType::Int64,
        KeyType::Float64 => array_type == arrow2::datatypes::DataType::Float64,
        KeyType::Utf8 => array_type == arrow2::datatypes::DataType::Utf8,
    };
    if ok {
        Ok(())
    } else {
        Err(JoinError::input(
            ErrorCode::TypeMismatch,
            format!("declared {declared:?} but built {array_type:?}"),
        ))
    }
}
