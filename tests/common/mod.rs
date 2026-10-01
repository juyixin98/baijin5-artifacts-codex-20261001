//! Independent test oracle.
#![allow(dead_code)]
//!
//! This module deliberately shares **no code** with the set operator core:
//! * fixtures are parsed with a standalone, line-oriented CSV parser written
//!   only for these tests,
//! * values are represented by a local [`RefScalar`],
//! * expected multisets are computed by naive HashMap multiplicity arithmetic
//!   (`+`, saturating `-`, `min`, presence).
//!
//! The production system's answers are converted back to the same scalar type
//! and compared as multisets (per-row multiplicity), which is the required
//! semantic equality.

use std::collections::HashMap;
use std::path::Path;

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum RefScalar {
    Null,
    Int(i64),
    /// Canonicalised f64 bits (NaN single pattern, -0 == +0).
    Float(u64),
    Bool(bool),
    Text(String),
}

impl RefScalar {
    fn parse(ty: &str, raw: &str, quoted: bool) -> RefScalar {
        let is_null = !quoted && (raw.is_empty() || raw == "\\N");
        if is_null {
            return RefScalar::Null;
        }
        match ty {
            "bigint" => RefScalar::Int(raw.parse().expect("reference bigint parse")),
            "double" => {
                let v: f64 = raw.parse().expect("reference double parse");
                let bits = if v.is_nan() {
                    f64::NAN.to_bits()
                } else if v == 0.0 {
                    0
                } else {
                    v.to_bits()
                };
                RefScalar::Float(bits)
            }
            "boolean" => RefScalar::Bool(matches!(
                raw.to_ascii_lowercase().as_str(),
                "true" | "t" | "1"
            )),
            "text" => RefScalar::Text(raw.to_string()),
            other => panic!("reference: unsupported type {other}"),
        }
    }
}

pub type Multiset = HashMap<Vec<RefScalar>, u64>;

pub fn add_row(ms: &mut Multiset, row: Vec<RefScalar>, delta: u64) {
    *ms.entry(row).or_insert(0) += delta;
}

/// Independent reference semantics for the six operators.
pub fn reference(op: &str, qualifier: &str, left: &Multiset, right: &Multiset) -> Multiset {
    let mut out = Multiset::new();
    let mut keys: Vec<&Vec<RefScalar>> = left.keys().collect();
    for k in right.keys() {
        if !left.contains_key(k) {
            keys.push(k);
        }
    }
    for k in keys {
        let l = left.get(k).copied();
        let r = right.get(k).copied();
        let n = match (op, qualifier) {
            ("UNION", "DISTINCT") => u64::from(l.is_some() || r.is_some()),
            ("UNION", "ALL") => l.unwrap_or(0) + r.unwrap_or(0),
            ("INTERSECT", "DISTINCT") => u64::from(l.is_some() && r.is_some()),
            ("INTERSECT", "ALL") => l.unwrap_or(0).min(r.unwrap_or(0)),
            ("EXCEPT", "DISTINCT") => u64::from(l.is_some() && r.is_none()),
            ("EXCEPT", "ALL") => l.unwrap_or(0).saturating_sub(r.unwrap_or(0)),
            _ => panic!("bad op/qualifier"),
        };
        if n > 0 {
            out.insert(k.clone(), n);
        }
    }
    out
}

/// Parse a typed fixture independently of production code.
pub fn parse_fixture(path: impl AsRef<Path>) -> (Vec<String>, Multiset) {
    let text = std::fs::read_to_string(path).expect("read fixture");
    let mut lines = text.lines().peekable();
    let header = lines.next().expect("header");
    let types: Vec<String> = header
        .split(',')
        .map(|h| {
            h.split_once(':')
                .expect("typed header")
                .1
                .trim()
                .to_string()
        })
        .collect();

    let mut ms = Multiset::new();
    for physical in lines.by_ref() {
        if physical.is_empty() {
            continue;
        }
        // A minimal independent parser: supports `""` and quoted commas.
        let fields = split_csv_line(physical);
        assert_eq!(fields.len(), types.len(), "fixture arity");
        let row: Vec<RefScalar> = types
            .iter()
            .zip(fields)
            .map(|(ty, (raw, quoted))| RefScalar::parse(ty, &raw, quoted))
            .collect();
        add_row(&mut ms, row, 1);
    }
    (types, ms)
}

fn split_csv_line(line: &str) -> Vec<(String, bool)> {
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut quoted = false;
    let mut ever_quoted = false;
    let mut chars = line.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '"' if quoted => {
                if chars.peek() == Some(&'"') {
                    cur.push('"');
                    chars.next();
                } else {
                    quoted = false;
                }
            }
            '"' if !quoted && cur.is_empty() => {
                quoted = true;
                ever_quoted = true;
            }
            ',' if !quoted => {
                out.push((std::mem::take(&mut cur), ever_quoted));
                ever_quoted = false;
            }
            other => cur.push(other),
        }
    }
    out.push((cur, ever_quoted));
    out
}

/// Convert production JSON output rows into reference scalars.
pub fn json_rows_to_multiset(rows: &[Vec<serde_json::Value>], types: &[String]) -> Multiset {
    let mut ms = Multiset::new();
    for row in rows {
        let scalars: Vec<RefScalar> = types
            .iter()
            .zip(row)
            .map(|(ty, v)| match (ty.as_str(), v) {
                (_, serde_json::Value::Null) => RefScalar::Null,
                ("bigint", serde_json::Value::Number(n)) => {
                    RefScalar::Int(n.as_i64().expect("i64"))
                }
                ("double", serde_json::Value::Number(n)) => {
                    let f = n.as_f64().expect("f64");
                    let bits = if f.is_nan() {
                        f64::NAN.to_bits()
                    } else if f == 0.0 {
                        0
                    } else {
                        f.to_bits()
                    };
                    RefScalar::Float(bits)
                }
                ("boolean", serde_json::Value::Bool(b)) => RefScalar::Bool(*b),
                ("text", serde_json::Value::String(s)) => RefScalar::Text(s.clone()),
                (t, x) => panic!("type/value mismatch {t} vs {x}"),
            })
            .collect();
        add_row(&mut ms, scalars, 1);
    }
    ms
}

pub fn assert_multisets_equal(got: &Multiset, want: &Multiset) {
    let mut only_got: Vec<_> = got.keys().filter(|k| !want.contains_key(*k)).collect();
    let mut only_want: Vec<_> = want.keys().filter(|k| !got.contains_key(*k)).collect();
    only_got.sort_by_key(|k| format!("{k:?}"));
    only_want.sort_by_key(|k| format!("{k:?}"));
    let mismatched: Vec<_> = got
        .iter()
        .filter_map(|(k, v)| {
            want.get(k)
                .map(|w| (k, v, w))
                .filter(|(_, a, b)| **a != **b)
        })
        .map(|(k, a, b)| format!("{k:?}: got {a}, want {b}"))
        .collect();
    assert!(
        only_got.is_empty() && only_want.is_empty() && mismatched.is_empty(),
        "multiset mismatch\n extra rows (got only): {only_got:?}\n missing rows: {only_want:?}\n multiplicity diffs: {mismatched:?}\n got size {}, want size {}",
        got.len(),
        want.len()
    );
}

/// Deterministic synthetic generator (xorshift) shared by stress tests.
pub struct Rng(pub u64);

impl Rng {
    pub fn next_u64(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.0 = x;
        x
    }
}
