//! Shared test utilities and an INDEPENDENT scalar 3VL oracle.
//!
//! The oracle works on plain Rust `Option<T>` values with hand-written Kleene
//! truth tables. It deliberately does not import `TriSet`/`Bitmap`, so expected
//! results are never produced by the implementation under test.

#![allow(dead_code)]

use serde_json::{Map, Value};
use tribool_index::batch::{ColumnSpec, ColumnType};
use tribool_index::index::Literal;
use tribool_index::query::Expr;

/// Oracle truth value — intentionally distinct from the crate's `Tri3`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Ora {
    T,
    F,
    U,
}

pub fn o_not(a: Ora) -> Ora {
    match a {
        Ora::T => Ora::F,
        Ora::F => Ora::T,
        Ora::U => Ora::U,
    }
}

pub fn o_and(a: Ora, b: Ora) -> Ora {
    match (a, b) {
        (Ora::T, Ora::T) => Ora::T,
        (Ora::F, _) | (_, Ora::F) => Ora::F,
        _ => Ora::U,
    }
}

pub fn o_or(a: Ora, b: Ora) -> Ora {
    match (a, b) {
        (Ora::F, Ora::F) => Ora::F,
        (Ora::T, _) | (_, Ora::T) => Ora::T,
        _ => Ora::U,
    }
}

/// A fixture row using only std Option types.
#[derive(Debug, Clone, PartialEq)]
pub struct OraRow {
    pub city: Option<String>,
    pub age: Option<i64>,
    pub score: Option<i64>,
    pub active: Option<bool>,
}

/// Scalar evaluation of a [`Expr`] over one plain row — the reference answer.
pub fn eval_scalar(expr: &Expr, row: &OraRow) -> Ora {
    match expr {
        Expr::Cmp { column, cmp, value } => {
            // Null tests take no literal; dispatch on the operator BEFORE
            // demanding a typed literal (otherwise the None literal panics).
            if cmp == "is_null" || cmp == "is_not_null" {
                let is_null = match column.as_str() {
                    "age" => row.age.is_none(),
                    "score" => row.score.is_none(),
                    "city" => row.city.is_none(),
                    "active" => row.active.is_none(),
                    other => panic!("oracle: unknown column {other}"),
                };
                let want_null = cmp == "is_null";
                return if is_null == want_null { Ora::T } else { Ora::F };
            }
            match column.as_str() {
                "age" => cmp_int(cmp, row.age, int_lit(value)),
                "score" => cmp_int(cmp, row.score, int_lit(value)),
                "city" => cmp_text(cmp, row.city.as_deref(), text_lit(value)),
                "active" => cmp_bool(cmp, row.active, bool_lit(value)),
                other => panic!("oracle: unknown column {other}"),
            }
        }
        Expr::Not { arg } => o_not(eval_scalar(arg, row)),
        Expr::And { args } => args.iter().map(|a| eval_scalar(a, row)).fold(Ora::T, o_and),
        Expr::Or { args } => args.iter().map(|a| eval_scalar(a, row)).fold(Ora::F, o_or),
    }
}

fn int_lit(v: &Option<Literal>) -> i64 {
    match v {
        Some(Literal::Int(n)) => *n,
        other => panic!("oracle expected int literal, got {other:?}"),
    }
}
fn text_lit(v: &Option<Literal>) -> String {
    match v {
        Some(Literal::Text(s)) => s.clone(),
        other => panic!("oracle expected text literal, got {other:?}"),
    }
}
fn bool_lit(v: &Option<Literal>) -> bool {
    match v {
        Some(Literal::Bool(b)) => *b,
        other => panic!("oracle expected bool literal, got {other:?}"),
    }
}

fn nullcheck(cmp: &str, is_null: bool) -> Ora {
    match cmp {
        "is_null" => {
            if is_null {
                Ora::T
            } else {
                Ora::F
            }
        }
        "is_not_null" => {
            if is_null {
                Ora::F
            } else {
                Ora::T
            }
        }
        other => panic!("oracle: null-only op {other}"),
    }
}

fn cmp_int(cmp: &str, v: Option<i64>, target: i64) -> Ora {
    match v {
        None if cmp == "is_null" || cmp == "is_not_null" => nullcheck(cmp, true),
        Some(_) if cmp == "is_null" || cmp == "is_not_null" => nullcheck(cmp, false),
        None => Ora::U,
        Some(v) => {
            let b = match cmp {
                "=" => v == target,
                "!=" => v != target,
                "<" => v < target,
                "<=" => v <= target,
                ">" => v > target,
                ">=" => v >= target,
                other => panic!("oracle: bad int op {other}"),
            };
            if b {
                Ora::T
            } else {
                Ora::F
            }
        }
    }
}

fn cmp_text(cmp: &str, v: Option<&str>, target: String) -> Ora {
    match v {
        None if cmp == "is_null" || cmp == "is_not_null" => nullcheck(cmp, true),
        Some(_) if cmp == "is_null" || cmp == "is_not_null" => nullcheck(cmp, false),
        None => Ora::U,
        Some(v) => {
            let b = match cmp {
                "=" => v == target,
                "!=" => v != target,
                "<" => v < target.as_str(),
                "<=" => v <= target.as_str(),
                ">" => v > target.as_str(),
                ">=" => v >= target.as_str(),
                other => panic!("oracle: bad text op {other}"),
            };
            if b {
                Ora::T
            } else {
                Ora::F
            }
        }
    }
}

fn cmp_bool(cmp: &str, v: Option<bool>, target: bool) -> Ora {
    match v {
        None if cmp == "is_null" || cmp == "is_not_null" => nullcheck(cmp, true),
        Some(_) if cmp == "is_null" || cmp == "is_not_null" => nullcheck(cmp, false),
        None => Ora::U,
        Some(v) => {
            let b = match cmp {
                "=" => v == target,
                "!=" => v != target,
                other => panic!("oracle: bad bool op {other}"),
            };
            if b {
                Ora::T
            } else {
                Ora::F
            }
        }
    }
}

/// Standard schema used across the suite.
pub fn people_spec() -> Vec<ColumnSpec> {
    vec![
        ColumnSpec {
            name: "city".into(),
            col_type: ColumnType::Text,
        },
        ColumnSpec {
            name: "age".into(),
            col_type: ColumnType::Int,
        },
        ColumnSpec {
            name: "score".into(),
            col_type: ColumnType::Int,
        },
        ColumnSpec {
            name: "active".into(),
            col_type: ColumnType::Bool,
        },
    ]
}

pub fn rows_to_json(rows: &[OraRow]) -> Vec<Map<String, Value>> {
    rows.iter()
        .map(|r| {
            let mut m = Map::new();
            m.insert("city".into(), opt_string(&r.city));
            m.insert("age".into(), opt_int(r.age));
            m.insert("score".into(), opt_int(r.score));
            m.insert("active".into(), opt_bool(r.active));
            m
        })
        .collect()
}

fn opt_string(v: &Option<String>) -> Value {
    v.clone().map(Value::String).unwrap_or(Value::Null)
}
fn opt_int(v: Option<i64>) -> Value {
    v.map(Value::from).unwrap_or(Value::Null)
}
fn opt_bool(v: Option<bool>) -> Value {
    v.map(Value::Bool).unwrap_or(Value::Null)
}

/// Initialize tracing once so test output can be correlated; each test passes a
/// distinct run identity in its messages.
pub fn init_tracing() {
    use std::sync::Once;
    static ONCE: Once = Once::new();
    ONCE.call_once(|| {
        let _ = tracing_subscriber::fmt()
            .with_env_filter(
                tracing_subscriber::EnvFilter::try_from_default_env()
                    .unwrap_or_else(|_| "debug,tribool_index=debug".into()),
            )
            .with_test_writer()
            .try_init();
    });
}

pub fn cmp(column: &str, op: &str, value: Option<Literal>) -> Expr {
    Expr::Cmp {
        column: column.into(),
        cmp: op.into(),
        value,
    }
}

pub fn intv(n: i64) -> Option<Literal> {
    Some(Literal::Int(n))
}
pub fn txtv(s: &str) -> Option<Literal> {
    Some(Literal::Text(s.into()))
}
pub fn boolv(b: bool) -> Option<Literal> {
    Some(Literal::Bool(b))
}
