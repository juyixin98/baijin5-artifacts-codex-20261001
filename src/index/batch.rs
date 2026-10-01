//! Typed batches built on Arrow2 arrays.
//!
//! Fixture CSV files are decoded into a single [`arrow2::chunk::Chunk`] whose
//! physical arrays match the declared logical types. All columns share one
//! row universe; per-cell validity bits are Arrow nulls and become the UNKNOWN
//! source for predicates.

use std::fs;

use arrow2::array::{Array, BooleanArray, PrimitiveArray, Utf8Array};
use arrow2::chunk::Chunk;

use crate::error::{Result, TviError};

/// Logical type declared in a fixture manifest.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LogicalType {
    Int,
    Float,
    Text,
    Bool,
}

impl LogicalType {
    /// Parse a manifest type name.
    pub fn parse(s: &str) -> Result<Self> {
        match s.trim().to_ascii_lowercase().as_str() {
            "int" | "i64" | "integer" | "bigint" => Ok(LogicalType::Int),
            "float" | "f64" | "double" | "real" => Ok(LogicalType::Float),
            "text" | "utf8" | "string" | "varchar" => Ok(LogicalType::Text),
            "bool" | "boolean" => Ok(LogicalType::Bool),
            other => Err(TviError::InvalidQuery(format!(
                "unknown column type {other:?} (expected int|float|text|bool)"
            ))),
        }
    }

    /// Arrow type name, used in schemas and logs.
    pub fn arrow_name(self) -> &'static str {
        match self {
            LogicalType::Int => "Int64",
            LogicalType::Float => "Float64",
            LogicalType::Text => "Utf8<Int32>",
            LogicalType::Bool => "Boolean",
        }
    }
}

/// Declared column metadata.
#[derive(Clone, Debug)]
pub struct ColumnMeta {
    pub name: String,
    pub logical: LogicalType,
}

/// A typed table: metadata plus one Arrow2 chunk covering the whole universe.
#[derive(Clone, Debug)]
pub struct TypedTable {
    pub name: String,
    pub columns: Vec<ColumnMeta>,
    pub chunk: Chunk<Box<dyn Array>>,
}

impl TypedTable {
    /// Build from raw CSV text and declared columns.
    ///
    /// An empty field is a NULL. Boolean literals accepted:
    /// `true/false/t/f/1/0/yes/no` (case-insensitive).
    pub fn from_csv(name: impl Into<String>, columns: Vec<ColumnMeta>, csv: &str) -> Result<Self> {
        let rows = parse_csv(csv)?;
        let mut header: Vec<String> = Vec::new();
        let mut data: Vec<Vec<String>> = Vec::new();
        for (line_no, rec) in rows.into_iter().enumerate() {
            if line_no == 0 {
                header = rec.into_iter().map(|s| s.trim().to_string()).collect();
                continue;
            }
            // A genuine blank separator line is a one-field record whose only
            // field is empty. An all-NULL data row has `columns.len()` empty
            // fields and must NOT be skipped (it is a real universe row).
            if rec.len() == 1 && rec[0].trim().is_empty() {
                continue;
            }
            if rec.len() != columns.len() {
                return Err(TviError::Io(format!(
                    "csv line {} has {} fields, expected {}",
                    line_no + 1,
                    rec.len(),
                    columns.len()
                )));
            }
            data.push(rec);
        }

        // The manifest (not the CSV header) is authoritative for types, but
        // header names must agree so a reordered fixture fails loudly.
        if !header.is_empty() && header.len() == columns.len() {
            for (h, c) in header.iter().zip(&columns) {
                if h != &c.name {
                    return Err(TviError::Io(format!(
                        "csv header {h:?} does not match declared column {:?}",
                        c.name
                    )));
                }
            }
        }

        let nrows = data.len();
        let arrays: Vec<Box<dyn Array>> = columns
            .iter()
            .map(|col| -> Result<Box<dyn Array>> {
                let ci = columns.iter().position(|c| c.name == col.name).unwrap();
                let cells = data.iter().map(|r| {
                    let s = r[ci].trim();
                    if s.is_empty() {
                        None
                    } else {
                        Some(s)
                    }
                });
                build_array(col, cells, nrows)
            })
            .collect::<Result<_>>()?;

        let chunk = Chunk::try_new(arrays).map_err(|e| TviError::Arrow(e.to_string()))?;
        Ok(TypedTable {
            name: name.into(),
            columns,
            chunk,
        })
    }

    /// Load a table from a CSV file.
    pub fn from_csv_path(
        name: impl Into<String>,
        columns: Vec<ColumnMeta>,
        path: &std::path::Path,
    ) -> Result<Self> {
        let csv = fs::read_to_string(path)?;
        Self::from_csv(name, columns, &csv)
    }

    /// Universe size.
    pub fn len(&self) -> usize {
        self.chunk.len()
    }

    /// Whether the table has zero rows.
    pub fn is_empty(&self) -> bool {
        self.chunk.len() == 0
    }

    /// Resolve a column by name.
    pub fn column(&self, name: &str) -> Option<(usize, &ColumnMeta)> {
        self.columns
            .iter()
            .position(|c| c.name == name)
            .map(|i| (i, &self.columns[i]))
    }

    /// Underlying Arrow array for column `i`.
    pub fn array(&self, i: usize) -> &dyn Array {
        self.chunk.arrays()[i].as_ref()
    }

    /// Extract one cell as a JSON value (null for NULL).
    pub fn cell_json(&self, col: usize, row: usize) -> serde_json::Value {
        let arr = self.array(col);
        if arr.validity().is_some_and(|v| !v.get_bit(row)) {
            return serde_json::Value::Null;
        }
        match self.columns[col].logical {
            LogicalType::Int => {
                let a = arr.as_any().downcast_ref::<PrimitiveArray<i64>>().unwrap();
                serde_json::Value::from(a.value(row))
            }
            LogicalType::Float => {
                let a = arr.as_any().downcast_ref::<PrimitiveArray<f64>>().unwrap();
                serde_json::Number::from_f64(a.value(row))
                    .map(serde_json::Value::Number)
                    .unwrap_or(serde_json::Value::Null)
            }
            LogicalType::Text => {
                let a = arr.as_any().downcast_ref::<Utf8Array<i32>>().unwrap();
                serde_json::Value::String(a.value(row).to_string())
            }
            LogicalType::Bool => {
                let a = arr.as_any().downcast_ref::<BooleanArray>().unwrap();
                serde_json::Value::Bool(a.value(row))
            }
        }
    }
}

fn build_array<'a>(
    col: &ColumnMeta,
    cells: impl Iterator<Item = Option<&'a str>>,
    nrows: usize,
) -> Result<Box<dyn Array>> {
    let line = |i: usize| format!("column `{}` row {}", col.name, i);
    match col.logical {
        LogicalType::Int => {
            let vals: Vec<Option<i64>> = cells
                .enumerate()
                .map(|(i, c)| match c {
                    None => Ok(None),
                    Some(s) => s
                        .parse::<i64>()
                        .map(Some)
                        .map_err(|e| TviError::Io(format!("{}: {e} ({s:?})", line(i + 1)))),
                })
                .collect::<Result<_>>()?;
            debug_assert_eq!(vals.len(), nrows);
            Ok(Box::new(PrimitiveArray::<i64>::from_iter(vals)))
        }
        LogicalType::Float => {
            let vals: Vec<Option<f64>> = cells
                .enumerate()
                .map(|(i, c)| match c {
                    None => Ok(None),
                    Some(s) => s
                        .parse::<f64>()
                        .map(Some)
                        .map_err(|e| TviError::Io(format!("{}: {e} ({s:?})", line(i + 1)))),
                })
                .collect::<Result<_>>()?;
            Ok(Box::new(PrimitiveArray::<f64>::from_iter(vals)))
        }
        LogicalType::Bool => {
            let vals: Vec<Option<bool>> = cells
                .enumerate()
                .map(|(i, c)| match c {
                    None => Ok(None),
                    Some(s) => match s.to_ascii_lowercase().as_str() {
                        "true" | "t" | "1" | "yes" => Ok(Some(true)),
                        "false" | "f" | "0" | "no" => Ok(Some(false)),
                        other => Err(TviError::Io(format!(
                            "{}: invalid boolean {other:?}",
                            line(i + 1)
                        ))),
                    },
                })
                .collect::<Result<_>>()?;
            Ok(Box::new(BooleanArray::from_iter(vals)))
        }
        LogicalType::Text => {
            let vals: Vec<Option<String>> = cells.map(|c| c.map(str::to_string)).collect();
            Ok(Box::new(Utf8Array::<i32>::from_iter(
                vals.iter().map(|v| v.as_deref()),
            )))
        }
    }
}

/// Minimal RFC-4180-ish CSV parser: quoted fields, escaped `""`, CRLF.
/// Returns records as `None`? No: empty fields stay `Some("")`; the caller
/// decides that a *trimmed* empty field is NULL.
fn parse_csv(input: &str) -> Result<Vec<Vec<String>>> {
    let mut out = Vec::new();
    let mut record = Vec::new();
    let mut field = String::new();
    let mut in_quotes = false;
    let mut chars = input.chars().peekable();
    let mut started = false;
    while let Some(c) = chars.next() {
        started = true;
        if in_quotes {
            match c {
                '"' => {
                    if chars.peek() == Some(&'"') {
                        field.push('"');
                        chars.next();
                    } else {
                        in_quotes = false;
                    }
                }
                other => field.push(other),
            }
        } else {
            match c {
                '"' if field.is_empty() => in_quotes = true,
                ',' => {
                    record.push(std::mem::take(&mut field));
                }
                '\n' => {
                    record.push(std::mem::take(&mut field));
                    out.push(std::mem::take(&mut record));
                }
                '\r' => {
                    if chars.peek() == Some(&'\n') {
                        chars.next();
                    }
                    record.push(std::mem::take(&mut field));
                    out.push(std::mem::take(&mut record));
                }
                other => field.push(other),
            }
        }
    }
    if in_quotes {
        return Err(TviError::Io("csv ends inside an unterminated quote".into()));
    }
    if started {
        record.push(field);
        out.push(record);
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cols() -> Vec<ColumnMeta> {
        vec![
            ColumnMeta {
                name: "id".into(),
                logical: LogicalType::Int,
            },
            ColumnMeta {
                name: "name".into(),
                logical: LogicalType::Text,
            },
            ColumnMeta {
                name: "active".into(),
                logical: LogicalType::Bool,
            },
        ]
    }

    #[test]
    fn decodes_nulls_and_quoted_fields() {
        let csv = "id,name,active\n1,\"Ada, B\",true\n2,,\n";
        let t = TypedTable::from_csv("t", cols(), csv).unwrap();
        assert_eq!(t.len(), 2);
        assert!(t.array(0).validity().is_none());
        assert!(t.array(1).validity().unwrap().get_bit(0));
        assert!(!t.array(1).validity().unwrap().get_bit(1));
        assert_eq!(t.cell_json(1, 0), serde_json::json!("Ada, B"));
        assert_eq!(t.cell_json(2, 1), serde_json::Value::Null);
        assert_eq!(t.cell_json(2, 0), serde_json::Value::Bool(true));
    }

    #[test]
    fn rejects_wrong_arity_and_bad_literals() {
        let csv = "id,name,active\n1,x,true,extra\n";
        assert!(TypedTable::from_csv("t", cols(), csv).is_err());
        let csv2 = "id,name,active\nnotan,x,true\n";
        assert!(TypedTable::from_csv("t", cols(), csv2).is_err());
    }

    #[test]
    fn all_null_multi_field_row_is_kept_as_a_universe_row() {
        // Two empty FIELDS is a real row whose nullable cells are NULL; only a
        // one-field empty record is a blank line.
        let csv = "id,name,active\n1,,true\n,,\n";
        let t = TypedTable::from_csv("t", cols(), csv).unwrap();
        assert_eq!(t.len(), 2, "all-NULL row must not be skipped");
        assert_eq!(t.cell_json(0, 1), serde_json::Value::Null);
        assert_eq!(t.cell_json(2, 1), serde_json::Value::Null);
        for c in 0..3 {
            assert!(!t.array(c).validity().unwrap().get_bit(1));
        }
    }
}
