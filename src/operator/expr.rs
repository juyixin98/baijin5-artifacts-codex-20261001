//! Typed evaluation of the restricted projection expression AST.
//!
//! The evaluator is intentionally total and explicit: every type mismatch or
//! overflow surfaces as an `invalid_data`/`internal` error rather than a panic.
//! Integer arithmetic uses checked ops (overflow is an explicit failure).

use std::collections::HashMap;

use crate::batch::Scalar;
use crate::error::{EngineError, EngineResult};
use crate::plan::Expr;

/// Named scalar environment for one side of the join.
pub type ColumnEnv<'a> = HashMap<&'a str, &'a Scalar>;

/// Build a name -> scalar map for a row with a declared schema.
pub fn env_for<'a>(schema: &'a [crate::plan::ColumnDecl], row: &'a [Scalar]) -> ColumnEnv<'a> {
    schema
        .iter()
        .zip(row)
        .map(|(c, v)| (c.name.as_str(), v))
        .collect()
}

/// Evaluate an expression against the recursive-view and edge-row environments.
pub fn evaluate(
    expr: &Expr,
    recursive: &ColumnEnv<'_>,
    edge: &ColumnEnv<'_>,
) -> EngineResult<Scalar> {
    match expr {
        Expr::RecursiveColumn { name } => recursive
            .get(name.as_str())
            .copied()
            .cloned()
            .ok_or_else(|| EngineError::internal(format!("unbound recursive column '{name}'"))),
        Expr::EdgeColumn { name } => edge
            .get(name.as_str())
            .copied()
            .cloned()
            .ok_or_else(|| EngineError::internal(format!("unbound edge column '{name}'"))),
        Expr::Literal { value } => literal_scalar(value),
        Expr::Add { left, right } => arith(left, right, recursive, edge, i64::checked_add, "add"),
        Expr::Sub { left, right } => arith(left, right, recursive, edge, i64::checked_sub, "sub"),
    }
}

fn literal_scalar(value: &serde_json::Value) -> EngineResult<Scalar> {
    match value {
        serde_json::Value::Null => Ok(Scalar::Null),
        serde_json::Value::Bool(b) => Ok(Scalar::Bool(*b)),
        serde_json::Value::Number(n) => n
            .as_i64()
            .map(Scalar::Int)
            .ok_or_else(|| EngineError::invalid_plan("integer literal out of int64 range")),
        serde_json::Value::String(s) => Ok(Scalar::Utf8(s.clone())),
        other => Err(EngineError::invalid_plan(format!(
            "unsupported literal type: {other}"
        ))),
    }
}

fn arith(
    left: &Expr,
    right: &Expr,
    recursive: &ColumnEnv<'_>,
    edge: &ColumnEnv<'_>,
    op: fn(i64, i64) -> Option<i64>,
    op_name: &str,
) -> EngineResult<Scalar> {
    let l = evaluate(left, recursive, edge)?;
    let r = evaluate(right, recursive, edge)?;
    match (l, r) {
        // SQL-style null propagation.
        (Scalar::Null, _) | (_, Scalar::Null) => Ok(Scalar::Null),
        (Scalar::Int(a), Scalar::Int(b)) => op(a, b).map(Scalar::Int).ok_or_else(|| {
            EngineError::invalid_data(format!("integer {op_name} overflow: {a} {b}"))
        }),
        (other_l, other_r) => Err(EngineError::invalid_data(format!(
            "'{op_name}' requires int64 operands, got {other_l:?} and {other_r:?}"
        ))),
    }
}

/// Verify a produced scalar fits the declared output column (null fits any).
pub fn check_output_type(
    value: &Scalar,
    declared: crate::plan::ColumnType,
    column: &str,
) -> EngineResult<()> {
    match value.runtime_type() {
        None => Ok(()),
        Some(actual) if actual == declared => Ok(()),
        Some(actual) => Err(EngineError::invalid_data(format!(
            "recursive projection for '{column}' produced {} but column is declared {}",
            actual.as_str(),
            declared.as_str()
        ))),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::plan::{ColumnDecl, ColumnType};
    use serde_json::json;

    fn decl(name: &str) -> ColumnDecl {
        ColumnDecl {
            name: name.to_string(),
            data_type: ColumnType::Int64,
        }
    }

    #[test]
    fn add_computes_and_null_propagates() {
        let schema = [decl("depth")];
        let row = [Scalar::Int(40)];
        let rec = env_for(&schema, &row);
        let edge: ColumnEnv = std::collections::HashMap::new();
        let expr = Expr::Add {
            left: Box::new(Expr::RecursiveColumn {
                name: "depth".into(),
            }),
            right: Box::new(Expr::Literal { value: json!(2) }),
        };
        assert_eq!(evaluate(&expr, &rec, &edge).unwrap(), Scalar::Int(42));

        let null_row = [Scalar::Null];
        let rec_n = env_for(&schema, &null_row);
        let expr_n = Expr::Add {
            left: Box::new(Expr::RecursiveColumn {
                name: "depth".into(),
            }),
            right: Box::new(Expr::Literal { value: json!(2) }),
        };
        assert_eq!(evaluate(&expr_n, &rec_n, &edge).unwrap(), Scalar::Null);
    }

    #[test]
    fn checked_overflow_is_an_error_not_a_panic() {
        let schema = [decl("depth")];
        let row = [Scalar::Int(1)];
        let rec = env_for(&schema, &row);
        let edge: ColumnEnv = std::collections::HashMap::new();
        let expr = Expr::Add {
            left: Box::new(Expr::RecursiveColumn {
                name: "depth".into(),
            }),
            right: Box::new(Expr::Literal {
                value: json!(i64::MAX),
            }),
        };
        let err = evaluate(&expr, &rec, &edge).unwrap_err();
        assert_eq!(err.category, crate::error::FailureCategory::InvalidData);
        assert!(err.message.contains("overflow"));
    }

    #[test]
    fn string_operand_to_add_is_a_typed_error() {
        let schema = [decl("depth")];
        let row = [Scalar::Int(1)];
        let rec = env_for(&schema, &row);
        let edge: ColumnEnv = std::collections::HashMap::new();
        let expr = Expr::Add {
            left: Box::new(Expr::RecursiveColumn {
                name: "depth".into(),
            }),
            right: Box::new(Expr::Literal { value: json!("x") }),
        };
        assert!(evaluate(&expr, &rec, &edge).is_err());
    }

    #[test]
    fn output_type_check_allows_null_in_any_column() {
        assert!(check_output_type(&Scalar::Null, ColumnType::Int64, "x").is_ok());
        assert!(check_output_type(&Scalar::Bool(true), ColumnType::Bool, "x").is_ok());
        assert!(check_output_type(&Scalar::Int(1), ColumnType::Bool, "x").is_err());
    }
}
