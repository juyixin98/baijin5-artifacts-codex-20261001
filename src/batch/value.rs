//! Scalar values shared by the execution engine and the typed-batch layer.
//!
//! [`Value`] is intentionally a small closed set: the supported recursive-CTE
//! subset only allows `Int64`, `Utf8`, `Boolean` and `Null`.

use std::fmt::{self, Write as _};

/// A single typed scalar.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum Value {
    /// SQL `NULL`.
    Null,
    /// 64-bit signed integer.
    Int64(i64),
    /// UTF-8 string.
    Utf8(String),
    /// Boolean.
    Boolean(bool),
}

impl Value {
    /// Stable type tag used in error messages and JSON output.
    pub fn type_name(&self) -> &'static str {
        match self {
            Value::Null => "Null",
            Value::Int64(_) => "Int64",
            Value::Utf8(_) => "Utf8",
            Value::Boolean(_) => "Boolean",
        }
    }

    /// Total order used for stable traversal.
    ///
    /// Ordering is type-discriminated first (so heterogeneous values never
    /// interleave), then by value. `NULL` sorts first.
    pub fn stable_cmp(&self, other: &Value) -> std::cmp::Ordering {
        use std::cmp::Ordering;
        match (self, other) {
            (Value::Null, Value::Null) => Ordering::Equal,
            (Value::Null, _) => Ordering::Less,
            (_, Value::Null) => Ordering::Greater,
            (Value::Boolean(a), Value::Boolean(b)) => a.cmp(b),
            (Value::Int64(a), Value::Int64(b)) => a.cmp(b),
            (Value::Utf8(a), Value::Utf8(b)) => a.cmp(b),
            (a, b) => a.discriminant().cmp(&b.discriminant()),
        }
    }

    fn discriminant(&self) -> u8 {
        match self {
            Value::Null => 0,
            Value::Boolean(_) => 1,
            Value::Int64(_) => 2,
            Value::Utf8(_) => 3,
        }
    }
    /// Render a path of key values in the canonical `[1,2,3]` / `["a","b"]` form.
    pub fn render_path(path: &[Value]) -> String {
        let mut out = String::from("[");
        for (i, v) in path.iter().enumerate() {
            if i > 0 {
                out.push(',');
            }
            match v {
                Value::Null => out.push_str("null"),
                Value::Int64(n) => {
                    let _ = write!(out, "{n}");
                }
                Value::Utf8(s) => {
                    // serde_json quoting gives us deterministic, escaped output.
                    out.push_str(&serde_json::to_string(s).unwrap_or_else(|_| "null".into()));
                }
                Value::Boolean(b) => out.push_str(if *b { "true" } else { "false" }),
            }
        }
        out.push(']');
        out
    }
}

impl fmt::Display for Value {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Value::Null => f.write_str("NULL"),
            Value::Int64(n) => write!(f, "{n}"),
            Value::Utf8(s) => write!(f, "{s}"),
            Value::Boolean(b) => write!(f, "{b}"),
        }
    }
}

impl PartialOrd for Value {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for Value {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        self.stable_cmp(other)
    }
}
