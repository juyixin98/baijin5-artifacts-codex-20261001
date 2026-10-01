//! Query expression AST and wire-format parsing.
//!
//! Wire JSON (see `fixtures/query_example.json`):
//!
//! ```json
//! {
//!   "as_of": 2,
//!   "where": {
//!     "op": "and",
//!     "args": [
//!       { "op": "cmp", "column": "age", "cmp": ">=", "value": 18 },
//!       { "op": "not", "arg": { "op": "is_null", "column": "name" } },
//!       { "op": "or", "args": [ ] }
//!     ]
//!   }
//! }
//! ```
//!
//! Parsing failures are [`crate::error::TviError::InvalidQuery`] with the
//! offending node quoted; binding failures (unknown column, bad literal) carry
//! their own category. A malformed query therefore never degrades to an empty
//! result.

use serde_json::Value;

use crate::error::{Result, TviError};
use crate::index::value_index::CmpOp;

/// One predicate-tree node.
#[derive(Clone, Debug)]
pub enum Expr {
    /// `column <cmp> value`.
    Cmp {
        column: String,
        op: CmpOp,
        value: Value,
    },
    /// `column IS NULL` when `negate == false`, `IS NOT NULL` when true.
    IsNull {
        column: String,
        negate: bool,
    },
    And(Vec<Expr>),
    Or(Vec<Expr>),
    Not(Box<Expr>),
}

impl Expr {
    /// Parse a `where` node out of the request JSON.
    pub fn parse(v: &Value) -> Result<Expr> {
        let obj = v.as_object().ok_or_else(|| {
            TviError::InvalidQuery(format!("predicate must be a JSON object, got {v}"))
        })?;
        let op = obj
            .get("op")
            .and_then(Value::as_str)
            .ok_or_else(|| TviError::InvalidQuery(format!("predicate {v} is missing string `op`")))?
            .to_ascii_lowercase();

        let parsed = match op.as_str() {
            "cmp" | "compare" => {
                let column = require_string(obj, "column", v)?;
                let cmp_raw = require_string(obj, "cmp", v)?;
                let op_cmp = CmpOp::parse(&cmp_raw)?;
                let value = obj
                    .get("value")
                    .ok_or_else(|| {
                        TviError::InvalidQuery(format!("cmp predicate {v} is missing `value`"))
                    })?
                    .clone();
                if value.is_null() {
                    return Err(TviError::InvalidQuery(format!(
                        "cmp predicate on `{column}` used JSON null; write `{{\"op\":\"is_null\",\"column\":\"{column}\"}}` instead"
                    )));
                }
                Expr::Cmp {
                    column,
                    op: op_cmp,
                    value,
                }
            }
            "is_null" | "isnull" => Expr::IsNull {
                column: require_string(obj, "column", v)?,
                negate: false,
            },
            "is_not_null" | "isnotnull" => Expr::IsNull {
                column: require_string(obj, "column", v)?,
                negate: true,
            },
            "and" | "or" => {
                let args = parse_args(obj.get("args"), v)?;
                if op == "and" {
                    Expr::And(args)
                } else {
                    Expr::Or(args)
                }
            }
            "not" => {
                let inner = obj.get("arg").ok_or_else(|| {
                    TviError::InvalidQuery(format!("not predicate {v} is missing `arg`"))
                })?;
                Expr::Not(Box::new(Expr::parse(inner)?))
            }
            other => {
                return Err(TviError::InvalidQuery(format!(
                "unknown predicate op {other:?} in {v} (want cmp|is_null|is_not_null|and|or|not)"
            )))
            }
        };
        Ok(parsed)
    }

    /// Human-readable rendering, used in trace logs so each step is
    /// attributable to a concrete expression.
    pub fn describe(&self) -> String {
        match self {
            Expr::Cmp { column, op, value } => format!("{column} {op} {value}"),
            Expr::IsNull { column, negate } => {
                if *negate {
                    format!("{column} IS NOT NULL")
                } else {
                    format!("{column} IS NULL")
                }
            }
            Expr::And(xs) => format!("AND({})", describe_list(xs)),
            Expr::Or(xs) => format!("OR({})", describe_list(xs)),
            Expr::Not(x) => format!("NOT({})", x.describe()),
        }
    }

    /// All column names referenced, for binding and error messages.
    pub fn columns(&self, out: &mut Vec<String>) {
        match self {
            Expr::Cmp { column, .. } | Expr::IsNull { column, .. } => out.push(column.clone()),
            Expr::And(xs) | Expr::Or(xs) => xs.iter().for_each(|x| x.columns(out)),
            Expr::Not(x) => x.columns(out),
        }
    }
}

fn describe_list(xs: &[Expr]) -> String {
    xs.iter().map(Expr::describe).collect::<Vec<_>>().join(", ")
}

fn require_string(obj: &serde_json::Map<String, Value>, key: &str, raw: &Value) -> Result<String> {
    match obj.get(key).and_then(Value::as_str) {
        Some(s) if !s.is_empty() => Ok(s.to_string()),
        _ => Err(TviError::InvalidQuery(format!(
            "predicate {raw} needs a non-empty string `{key}`"
        ))),
    }
}

fn parse_args(slot: Option<&Value>, raw: &Value) -> Result<Vec<Expr>> {
    let arr = slot
        .and_then(Value::as_array)
        .ok_or_else(|| TviError::InvalidQuery(format!("{raw} needs an array `args`")))?;
    if arr.is_empty() {
        return Err(TviError::InvalidQuery(format!(
            "{raw} has an empty `args` list; boolean operators need at least one predicate"
        )));
    }
    arr.iter().map(Expr::parse).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_full_tree() {
        let v: Value = serde_json::json!({
            "op": "and",
            "args": [
                {"op": "cmp", "column": "age", "cmp": ">=", "value": 18},
                {"op": "not", "arg": {"op": "is_null", "column": "name"}}
            ]
        });
        let e = Expr::parse(&v).unwrap();
        assert!(matches!(e, Expr::And(_)));
        assert!(e.describe().contains("NOT(name IS NULL)"));
        let mut cols = Vec::new();
        e.columns(&mut cols);
        assert_eq!(cols, vec!["age", "name"]);
    }

    #[test]
    fn explicit_error_categories_for_bad_inputs() {
        let cases = [
            serde_json::json!("nope"),
            serde_json::json!({"op": "cmp", "column": "a", "cmp": "~", "value": 1}),
            serde_json::json!({"op": "cmp", "column": "a", "cmp": "=", "value": null}),
            serde_json::json!({"op": "and", "args": []}),
            serde_json::json!({"op": "not"}),
            serde_json::json!({"op": "wat"}),
        ];
        for c in cases {
            assert!(
                matches!(Expr::parse(&c), Err(TviError::InvalidQuery(_))),
                "expected InvalidQuery for {c}"
            );
        }
    }
}
