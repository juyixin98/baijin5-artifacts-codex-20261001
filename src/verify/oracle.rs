//! Independent scalar oracle.
//!
//! This module deliberately shares **no code** with the engine under test:
//! it parses the manifest and CSV itself (its own parser below), models cells
//! as `Option<OVal>`, walks the wire JSON itself, and computes SQL 3VL one row
//! at a time with plain scalar `match`es. The bitmap / [`crate::index::Tricolor`]
//! core cannot influence these answers — the cross-check in [`super::runner`]
//! is therefore a real differential test, not a self-consistency check.

use std::collections::HashMap;
use std::path::Path;

use serde::Deserialize;

/// Scalar truth value, independent of `crate::index::tricolor::Tri`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum OT {
    True,
    False,
    Unknown,
}

/// Scalar cell value, independent of the Arrow typed batch.
#[derive(Clone, Debug, PartialEq)]
pub enum OVal {
    Int(i64),
    Float(f64),
    Text(String),
    Bool(bool),
}

#[derive(Deserialize)]
struct OracleManifest {
    csv: String,
    #[allow(dead_code)]
    table: String,
    #[serde(default = "one")]
    content_version: u64,
    columns: Vec<OColumn>,
    #[serde(default)]
    deletes: Vec<ODelete>,
}

fn one() -> u64 {
    1
}

#[derive(Deserialize)]
struct OColumn {
    name: String,
    #[serde(rename = "type")]
    ty: String,
}

#[derive(Deserialize)]
struct ODelete {
    row: usize,
    version: u64,
}

/// A fixture read entirely independently of the engine.
#[derive(Debug)]
pub struct OracleFixture {
    pub content_version: u64,
    pub types: HashMap<String, String>,
    /// rows[row][column] = scalar cell (None == SQL NULL).
    pub rows: Vec<HashMap<String, Option<OVal>>>,
    pub deletes: Vec<(usize, u64)>,
}

impl OracleFixture {
    /// Parse manifest + CSV using only this module's code.
    pub fn load(fixture_dir: &Path, manifest_path: &Path) -> anyhow_lite::Result<Self> {
        let manifest_text = std::fs::read_to_string(manifest_path)
            .map_err(|e| format!("read manifest {}: {e}", manifest_path.display()))?;
        let manifest: OracleManifest = toml::from_str(&manifest_text)
            .map_err(|e| format!("parse manifest {}: {e}", manifest_path.display()))?;
        if manifest.columns.is_empty() {
            return Err("manifest has no columns".into());
        }
        let csv_path = fixture_dir.join(&manifest.csv);
        let csv_text = std::fs::read_to_string(&csv_path)
            .map_err(|e| format!("read csv {}: {e}", csv_path.display()))?;

        let records = parse_csv_standalone(&csv_text)?;
        let header = &records[0];
        if header.len() != manifest.columns.len() {
            return Err(format!(
                "csv has {} header columns, manifest declares {}",
                header.len(),
                manifest.columns.len()
            )
            .into());
        }
        for (h, c) in header.iter().zip(&manifest.columns) {
            if h.trim() != c.name {
                return Err(format!("csv header {h:?} != manifest column {:?}", c.name).into());
            }
        }

        let types: HashMap<String, String> = manifest
            .columns
            .iter()
            .map(|c| (c.name.clone(), c.ty.to_ascii_lowercase()))
            .collect();

        let mut rows = Vec::new();
        for (line_no, rec) in records.iter().enumerate().skip(1) {
            // Only a one-field empty record is a blank line; a multi-field
            // record of empty cells is an all-NULL data row and must count.
            if rec.len() == 1 && rec[0].trim().is_empty() {
                continue;
            }
            if rec.len() != manifest.columns.len() {
                return Err(format!(
                    "csv line {} has {} fields, expected {}",
                    line_no + 1,
                    rec.len(),
                    manifest.columns.len()
                )
                .into());
            }
            let mut row = HashMap::new();
            for (col, raw) in manifest.columns.iter().zip(rec) {
                let cell = parse_cell(&col.ty, raw.trim(), line_no + 1, &col.name)?;
                row.insert(col.name.clone(), cell);
            }
            rows.push(row);
        }

        Ok(OracleFixture {
            content_version: manifest.content_version,
            types,
            rows,
            deletes: manifest
                .deletes
                .into_iter()
                .map(|d| (d.row, d.version))
                .collect(),
        })
    }

    /// Independent live-set computation at `as_of`.
    pub fn is_live_at(&self, row: usize, as_of: u64) -> bool {
        if self.content_version > as_of {
            return false;
        }
        for (r, v) in &self.deletes {
            if *r == row && *v <= as_of {
                return false;
            }
        }
        true
    }

    /// Evaluate one wire predicate for one row. Independent 3VL semantics.
    pub fn eval_row(&self, expr: &serde_json::Value, row: usize) -> Result<OT, String> {
        let obj = expr
            .as_object()
            .ok_or_else(|| format!("predicate not an object: {expr}"))?;
        let op = obj
            .get("op")
            .and_then(|v| v.as_str())
            .ok_or_else(|| format!("predicate missing op: {expr}"))?
            .to_ascii_lowercase();
        match op.as_str() {
            "cmp" | "compare" => {
                let col = field_str(obj, "column", expr)?;
                let cmp = field_str(obj, "cmp", expr)?;
                let lit = obj
                    .get("value")
                    .ok_or_else(|| format!("cmp missing value: {expr}"))?;
                if lit.is_null() {
                    return Err(format!("NULL literal is illegal in cmp on `{col}`"));
                }
                let cell = self
                    .rows
                    .get(row)
                    .and_then(|r| r.get(col))
                    .ok_or_else(|| format!("unknown column {col:?}"))?;
                match cell {
                    None => Ok(OT::Unknown), // NULL compared with anything -> UNKNOWN
                    Some(v) => Ok(compare_scalar(v, lit, cmp, col)?),
                }
            }
            "is_null" | "isnotnull" | "is_not_null" => {
                let col = field_str(obj, "column", expr)?;
                let negate = op == "is_not_null" || op == "isnotnull";
                let cell = self
                    .rows
                    .get(row)
                    .and_then(|r| r.get(col))
                    .ok_or_else(|| format!("unknown column {col:?}"))?;
                let is_null = cell.is_none();
                Ok(match (is_null, negate) {
                    (true, false) | (false, true) => OT::True,
                    _ => OT::False,
                })
            }
            "not" => {
                let inner = obj
                    .get("arg")
                    .ok_or_else(|| format!("not missing arg: {expr}"))?;
                Ok(match self.eval_row(inner, row)? {
                    OT::True => OT::False,
                    OT::False => OT::True,
                    OT::Unknown => OT::Unknown,
                })
            }
            "and" => {
                let args = field_args(obj, expr)?;
                let mut acc = OT::True;
                for a in args {
                    acc = kleene_and(acc, self.eval_row(a, row)?);
                }
                Ok(acc)
            }
            "or" => {
                let args = field_args(obj, expr)?;
                let mut acc = OT::False;
                for a in args {
                    acc = kleene_or(acc, self.eval_row(a, row)?);
                }
                Ok(acc)
            }
            other => Err(format!("unknown predicate op {other:?}")),
        }
    }
}

/// Kleene AND, written out explicitly as the truth table.
fn kleene_and(a: OT, b: OT) -> OT {
    match (a, b) {
        (OT::False, _) | (_, OT::False) => OT::False,
        (OT::True, OT::True) => OT::True,
        _ => OT::Unknown,
    }
}

/// Kleene OR, written out explicitly as the truth table.
fn kleene_or(a: OT, b: OT) -> OT {
    match (a, b) {
        (OT::True, _) | (_, OT::True) => OT::True,
        (OT::False, OT::False) => OT::False,
        _ => OT::Unknown,
    }
}

fn compare_scalar(
    cell: &OVal,
    lit: &serde_json::Value,
    cmp: &str,
    col: &str,
) -> Result<OT, String> {
    let ord = match (cell, lit) {
        (OVal::Int(a), serde_json::Value::Number(n)) => {
            let b = n.as_i64().ok_or_else(|| type_err(col, "integer", lit))?;
            a.cmp(&b)
        }
        (OVal::Float(a), serde_json::Value::Number(n)) => {
            let b = n.as_f64().ok_or_else(|| type_err(col, "float", lit))?;
            a.partial_cmp(&b)
                .ok_or_else(|| format!("unordered float on column `{col}`"))?
        }
        (OVal::Text(a), serde_json::Value::String(b)) => a.cmp(b),
        (OVal::Bool(a), serde_json::Value::Bool(b)) => a.cmp(b),
        _ => return Err(type_err(col, "column value", lit)),
    };
    use std::cmp::Ordering::*;
    let hit = match cmp {
        "=" | "==" | "eq" => ord == Equal,
        "<>" | "!=" | "neq" => ord != Equal,
        "<" | "lt" => ord == Less,
        "<=" | "le" => ord != Greater,
        ">" | "gt" => ord == Greater,
        ">=" | "ge" => ord != Less,
        other => return Err(format!("unknown comparison {other:?} on `{col}`")),
    };
    Ok(if hit { OT::True } else { OT::False })
}

fn type_err(col: &str, expected: &str, lit: &serde_json::Value) -> String {
    format!("type mismatch on `{col}`: expected {expected}, got JSON {lit}")
}

fn field_str<'a>(
    obj: &'a serde_json::Map<String, serde_json::Value>,
    key: &str,
    raw: &serde_json::Value,
) -> Result<&'a str, String> {
    obj.get(key)
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .ok_or_else(|| format!("predicate needs string `{key}`: {raw}"))
}

fn field_args<'a>(
    obj: &'a serde_json::Map<String, serde_json::Value>,
    raw: &serde_json::Value,
) -> Result<&'a Vec<serde_json::Value>, String> {
    let args = obj
        .get("args")
        .and_then(|v| v.as_array())
        .ok_or_else(|| format!("predicate needs array args: {raw}"))?;
    if args.is_empty() {
        return Err(format!("empty args in {raw}"));
    }
    Ok(args)
}

fn parse_cell(ty: &str, raw: &str, line: usize, col: &str) -> Result<Option<OVal>, String> {
    if raw.is_empty() {
        return Ok(None);
    }
    let bad = |e: String| format!("{col} row {line}: {e} (cell {raw:?})");
    let val = match ty {
        "int" | "i64" | "integer" | "bigint" => {
            OVal::Int(raw.parse::<i64>().map_err(|e| bad(e.to_string()))?)
        }
        "float" | "f64" | "double" | "real" => {
            OVal::Float(raw.parse::<f64>().map_err(|e| bad(e.to_string()))?)
        }
        "text" | "utf8" | "string" | "varchar" => OVal::Text(raw.to_string()),
        "bool" | "boolean" => OVal::Bool(match raw.to_ascii_lowercase().as_str() {
            "true" | "t" | "1" | "yes" => true,
            "false" | "f" | "0" | "no" => false,
            other => return Err(bad(format!("bad boolean {other:?}"))),
        }),
        other => return Err(format!("unknown type {other:?}")),
    };
    Ok(Some(val))
}

/// Standalone RFC-ish CSV parser (quoted fields, `""`, CRLF). Intentionally a
/// separate implementation from the engine's parser.
fn parse_csv_standalone(input: &str) -> Result<Vec<Vec<String>>, String> {
    let mut records = vec![vec![String::new()]];
    let mut quoted = false;
    let mut chars = input.chars().peekable();
    while let Some(c) = chars.next() {
        let rec = records.last_mut().unwrap();
        if quoted {
            match c {
                '"' if chars.peek() == Some(&'"') => {
                    rec.last_mut().unwrap().push('"');
                    chars.next();
                }
                '"' => quoted = false,
                other => rec.last_mut().unwrap().push(other),
            }
        } else {
            match c {
                '"' if rec.last().map(|s| s.is_empty()).unwrap_or(true) => quoted = true,
                ',' => rec.push(String::new()),
                '\n' => records.push(vec![String::new()]),
                '\r' => {
                    if chars.peek() == Some(&'\n') {
                        chars.next();
                    }
                    records.push(vec![String::new()]);
                }
                other => rec.last_mut().unwrap().push(other),
            }
        }
    }
    if quoted {
        return Err("unterminated quote in csv".into());
    }
    // Drop a single trailing empty record produced by the final newline.
    if records
        .last()
        .is_some_and(|r| r.len() == 1 && r[0].is_empty())
    {
        records.pop();
    }
    Ok(records)
}

/// Minimal local error alias so the oracle needs no external error crate.
pub mod anyhow_lite {
    /// Boxed dyn error, std-only.
    pub type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;
}
