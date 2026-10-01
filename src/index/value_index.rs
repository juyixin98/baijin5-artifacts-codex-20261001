//! Column-value bitmap indexes.
//!
//! One [`ColumnIndex`] per typed column:
//! * a **NULL bitmap** (rows that evaluate to UNKNOWN for every comparison
//!   against a literal),
//! * per-value equality bitmaps — a hash of distinct values for integers/text,
//!   two slots for booleans, a bit-pattern key for floats,
//! * range comparisons produced by scanning the typed Arrow array.
//!
//! Every lookup returns a [`Tricolor`] over the index universe: NULL rows are
//! UNKNOWN, matching rows TRUE, the rest FALSE. The index also records the
//! generation it was built at; combining indexes across generations is
//! rejected by [`crate::index::version::VersionMap::ensure_compatible`].

use std::collections::HashMap;

use arrow2::array::{Array, BooleanArray, PrimitiveArray, Utf8Array};

use super::batch::{LogicalType, TypedTable};
use super::bitmap::Bitmap;
use super::tricolor::Tricolor;
use super::version::Version;
use crate::error::{Result, TviError};

/// Scalar literal in a predicate.
#[derive(Clone, Debug, PartialEq)]
pub enum Scalar {
    Int(i64),
    Float(f64),
    Text(String),
    Bool(bool),
}

impl Scalar {
    /// Coerce a JSON literal into the column's physical type.
    pub fn coerce(logical: LogicalType, raw: &serde_json::Value) -> Result<Scalar> {
        let err = || TviError::InvalidLiteral {
            column: String::new(),
            value: raw.to_string(),
        };
        match (logical, raw) {
            (LogicalType::Int, serde_json::Value::Number(n)) => n
                .as_i64()
                .map(Scalar::Int)
                .ok_or_else(|| TviError::InvalidLiteral {
                    column: String::new(),
                    value: raw.to_string(),
                }),
            (LogicalType::Float, serde_json::Value::Number(n)) => {
                n.as_f64().map(Scalar::Float).ok_or_else(err)
            }
            (LogicalType::Text, serde_json::Value::String(s)) => Ok(Scalar::Text(s.clone())),
            (LogicalType::Bool, serde_json::Value::Bool(b)) => Ok(Scalar::Bool(*b)),
            _ => Err(TviError::TypeMismatch {
                column: String::new(),
                expected: logical.arrow_name(),
                found: json_type_name(raw),
            }),
        }
    }
}

fn json_type_name(v: &serde_json::Value) -> &'static str {
    match v {
        serde_json::Value::Null => "null (use IS NULL instead of a comparison)",
        serde_json::Value::Bool(_) => "boolean",
        serde_json::Value::Number(_) => "number",
        serde_json::Value::String(_) => "string",
        serde_json::Value::Array(_) => "array",
        serde_json::Value::Object(_) => "object",
    }
}

/// Comparison operators usable against a column.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CmpOp {
    Eq,
    Neq,
    Lt,
    Le,
    Gt,
    Ge,
}

impl CmpOp {
    /// Parse the wire/JSON spelling.
    pub fn parse(s: &str) -> Result<Self> {
        match s {
            "=" | "eq" | "==" => Ok(CmpOp::Eq),
            "<>" | "!=" | "neq" => Ok(CmpOp::Neq),
            "<" | "lt" => Ok(CmpOp::Lt),
            "<=" | "le" => Ok(CmpOp::Le),
            ">" | "gt" => Ok(CmpOp::Gt),
            ">=" | "ge" => Ok(CmpOp::Ge),
            other => Err(TviError::InvalidQuery(format!(
                "unknown comparison operator {other:?}"
            ))),
        }
    }

    /// Wire spelling.
    pub fn as_str(self) -> &'static str {
        match self {
            CmpOp::Eq => "=",
            CmpOp::Neq => "<>",
            CmpOp::Lt => "<",
            CmpOp::Le => "<=",
            CmpOp::Gt => ">",
            CmpOp::Ge => ">=",
        }
    }

    fn apply_i64(self, a: i64, b: i64) -> bool {
        match self {
            CmpOp::Eq => a == b,
            CmpOp::Neq => a != b,
            CmpOp::Lt => a < b,
            CmpOp::Le => a <= b,
            CmpOp::Gt => a > b,
            CmpOp::Ge => a >= b,
        }
    }

    fn apply_f64(self, a: f64, b: f64) -> bool {
        // NaN never satisfies a comparison on known rows (SQL treats it as
        // unordered); NULLs are handled separately as UNKNOWN.
        if a.is_nan() || b.is_nan() {
            return false;
        }
        match self {
            CmpOp::Eq => a == b,
            CmpOp::Neq => a != b,
            CmpOp::Lt => a < b,
            CmpOp::Le => a <= b,
            CmpOp::Gt => a > b,
            CmpOp::Ge => a >= b,
        }
    }
}

impl std::fmt::Display for CmpOp {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Per-value equality maps, one variant per physical type.
#[derive(Debug)]
enum ValueMap {
    /// distinct int -> rows
    Int(HashMap<i64, Bitmap>),
    /// distinct float bit-pattern -> rows (NaN intentionally absent from eq hits)
    Float(HashMap<u64, Bitmap>),
    /// distinct text -> rows
    Text(HashMap<String, Bitmap>),
    /// [false rows, true rows]
    Bool([Bitmap; 2]),
}

/// Bitmap index for one column at one generation.
#[derive(Debug)]
pub struct ColumnIndex {
    name: String,
    logical: LogicalType,
    len: usize,
    version: Version,
    null: Bitmap,
    values: ValueMap,
}

impl ColumnIndex {
    /// Build the index from column `col` of `table`, stamped with `version`.
    pub fn build(table: &TypedTable, col: usize, version: Version) -> Result<Self> {
        let meta = &table.columns[col];
        let arr = table.array(col);
        let len = table.len();
        let null = validity_bitmap(arr, len);
        let values = match meta.logical {
            LogicalType::Int => {
                let a = arr
                    .as_any()
                    .downcast_ref::<PrimitiveArray<i64>>()
                    .expect("Int column must be Int64Array");
                let mut map: HashMap<i64, Bitmap> = HashMap::new();
                for row in 0..len {
                    if !null.get(row) {
                        set_map_bit(&mut map, a.value(row), row, len)?;
                    }
                }
                ValueMap::Int(map)
            }
            LogicalType::Float => {
                let a = arr
                    .as_any()
                    .downcast_ref::<PrimitiveArray<f64>>()
                    .expect("Float column must be Float64Array");
                let mut map: HashMap<u64, Bitmap> = HashMap::new();
                for row in 0..len {
                    if !null.get(row) {
                        // NaN rows are reachable only through scans, never an eq key.
                        if !a.value(row).is_nan() {
                            set_map_bit(&mut map, a.value(row).to_bits(), row, len)?;
                        }
                    }
                }
                ValueMap::Float(map)
            }
            LogicalType::Text => {
                let a = arr
                    .as_any()
                    .downcast_ref::<Utf8Array<i32>>()
                    .expect("Text column must be Utf8Array<i32>");
                let mut map: HashMap<String, Bitmap> = HashMap::new();
                for row in 0..len {
                    if !null.get(row) {
                        set_map_bit(&mut map, a.value(row).to_string(), row, len)?;
                    }
                }
                ValueMap::Text(map)
            }
            LogicalType::Bool => {
                let a = arr
                    .as_any()
                    .downcast_ref::<BooleanArray>()
                    .expect("Bool column must be BooleanArray");
                let mut slots = [Bitmap::zeros(len), Bitmap::zeros(len)];
                for row in 0..len {
                    if !null.get(row) {
                        slots[usize::from(a.value(row))].set(row)?;
                    }
                }
                ValueMap::Bool(slots)
            }
        };
        Ok(ColumnIndex {
            name: meta.name.clone(),
            logical: meta.logical,
            len,
            version,
            null,
            values,
        })
    }

    /// Column name.
    pub fn name(&self) -> &str {
        &self.name
    }

    /// Physical/logical type.
    pub fn logical_type(&self) -> LogicalType {
        self.logical
    }

    /// Universe size.
    pub fn len(&self) -> usize {
        self.len
    }

    /// Whether the column has zero rows.
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    /// Generation the index was built at.
    pub fn version(&self) -> Version {
        self.version
    }

    /// NULL (UNKNOWN) mask.
    pub fn null_mask(&self) -> &Bitmap {
        &self.null
    }

    /// `column IS NULL` / `column IS NOT NULL`: never produces UNKNOWN.
    pub fn evaluate_is_null(&self, negate: bool) -> Tricolor {
        if negate {
            Tricolor::from_true(self.null.complement())
        } else {
            Tricolor::from_true(self.null.clone())
        }
    }

    /// Evaluate `column <op> literal` under SQL 3VL.
    pub fn evaluate(&self, op: CmpOp, scalar: &Scalar) -> Result<Tricolor> {
        self.check_scalar_type(scalar)?;
        let matched = match (&self.values, scalar) {
            (ValueMap::Int(map), Scalar::Int(v)) => match op {
                CmpOp::Eq => map
                    .get(v)
                    .cloned()
                    .unwrap_or_else(|| Bitmap::zeros(self.len)),
                CmpOp::Neq => map
                    .get(v)
                    .cloned()
                    .unwrap_or_else(|| Bitmap::zeros(self.len))
                    .complement()
                    .and(&self.non_null())?,
                _ => self.scan_i64(op, *v)?,
            },
            (ValueMap::Float(map), Scalar::Float(v)) => match op {
                CmpOp::Eq => {
                    if v.is_nan() {
                        Bitmap::zeros(self.len)
                    } else {
                        map.get(&v.to_bits())
                            .cloned()
                            .unwrap_or_else(|| Bitmap::zeros(self.len))
                    }
                }
                CmpOp::Neq => {
                    // SQL: NULL <> v stays UNKNOWN; NaN <> v is TRUE on known rows.
                    let eq = if v.is_nan() {
                        Bitmap::zeros(self.len)
                    } else {
                        map.get(&v.to_bits())
                            .cloned()
                            .unwrap_or_else(|| Bitmap::zeros(self.len))
                    };
                    self.non_null().and_not(&eq)?
                }
                _ => self.scan_f64(op, *v)?,
            },
            (ValueMap::Text(map), Scalar::Text(v)) => match op {
                CmpOp::Eq => map
                    .get(v)
                    .cloned()
                    .unwrap_or_else(|| Bitmap::zeros(self.len)),
                CmpOp::Neq => {
                    let eq = map
                        .get(v)
                        .cloned()
                        .unwrap_or_else(|| Bitmap::zeros(self.len));
                    self.non_null().and_not(&eq)?
                }
                CmpOp::Lt | CmpOp::Le | CmpOp::Gt | CmpOp::Ge => self.scan_text(op, v)?,
            },
            (ValueMap::Bool(slots), Scalar::Bool(v)) => {
                let truthy = slots[usize::from(*v)].clone();
                match op {
                    CmpOp::Eq => truthy,
                    CmpOp::Neq => self.non_null().and_not(&truthy)?,
                    _ => {
                        return Err(TviError::InvalidQuery(
                            "booleans only support = and <>".into(),
                        ))
                    }
                }
            }
            _ => unreachable!("type compatibility checked above"),
        };
        Tricolor::from_true_and_null(matched, self.null.clone())
    }

    fn non_null(&self) -> Bitmap {
        self.null.complement()
    }

    fn check_scalar_type(&self, s: &Scalar) -> Result<()> {
        let ok = matches!(
            (self.logical, s),
            (LogicalType::Int, Scalar::Int(_))
                | (LogicalType::Float, Scalar::Float(_))
                | (LogicalType::Text, Scalar::Text(_))
                | (LogicalType::Bool, Scalar::Bool(_))
        );
        if ok {
            Ok(())
        } else {
            Err(TviError::TypeMismatch {
                column: self.name.clone(),
                expected: self.logical.arrow_name(),
                found: scalar_type_name(s),
            })
        }
    }

    fn scan_i64(&self, op: CmpOp, v: i64) -> Result<Bitmap> {
        // Range scans rebuild from the equality map keys so the path stays
        // index-driven (no raw array retained after build).
        let ValueMap::Int(map) = &self.values else {
            unreachable!()
        };
        let mut out = Bitmap::zeros(self.len);
        for (val, rows) in map {
            if op.apply_i64(*val, v) {
                out = out.or(rows)?;
            }
        }
        Ok(out)
    }

    fn scan_f64(&self, op: CmpOp, v: f64) -> Result<Bitmap> {
        let ValueMap::Float(map) = &self.values else {
            unreachable!()
        };
        let mut out = Bitmap::zeros(self.len);
        for (bits, rows) in map {
            let val = f64::from_bits(*bits);
            if op.apply_f64(val, v) {
                out = out.or(rows)?;
            }
        }
        Ok(out)
    }

    fn scan_text(&self, op: CmpOp, v: &str) -> Result<Bitmap> {
        let ValueMap::Text(map) = &self.values else {
            unreachable!()
        };
        let mut out = Bitmap::zeros(self.len);
        for (val, rows) in map {
            let hit = match op {
                CmpOp::Lt => val.as_str() < v,
                CmpOp::Le => val.as_str() <= v,
                CmpOp::Gt => val.as_str() > v,
                CmpOp::Ge => val.as_str() >= v,
                _ => unreachable!(),
            };
            if hit {
                out = out.or(rows)?;
            }
        }
        Ok(out)
    }
}

fn scalar_type_name(s: &Scalar) -> &'static str {
    match s {
        Scalar::Int(_) => "integer",
        Scalar::Float(_) => "float",
        Scalar::Text(_) => "text",
        Scalar::Bool(_) => "boolean",
    }
}

/// Extract Arrow validity as a zeroed-tail bitmap.
fn validity_bitmap(arr: &dyn Array, len: usize) -> Bitmap {
    match arr.validity() {
        None => Bitmap::zeros(len),
        Some(validity) => {
            let ones: Vec<usize> = (0..len).filter(|&r| !validity.get_bit(r)).collect();
            Bitmap::from_indices(len, ones).expect("validity indices in range")
        }
    }
}

fn set_map_bit<K: Eq + std::hash::Hash>(
    map: &mut HashMap<K, Bitmap>,
    key: K,
    row: usize,
    len: usize,
) -> Result<()> {
    let bm = map.entry(key).or_insert_with(|| Bitmap::zeros(len));
    bm.set(row)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::index::batch::ColumnMeta;

    fn table() -> TypedTable {
        let cols = vec![
            ColumnMeta {
                name: "a".into(),
                logical: LogicalType::Int,
            },
            ColumnMeta {
                name: "s".into(),
                logical: LogicalType::Text,
            },
        ];
        let csv = "a,s\n10,x\n20,\n,y\n10,\n";
        TypedTable::from_csv("t", cols, csv).unwrap()
    }

    #[test]
    fn equality_indexes_and_null_unknown_semantics() {
        let t = table();
        let idx = ColumnIndex::build(&t, 0, 1).unwrap();
        let r = idx.evaluate(CmpOp::Eq, &Scalar::Int(10)).unwrap();
        // rows: 0=10 TRUE, 1=20 FALSE, 2=NULL UNKNOWN, 3=10 TRUE.
        assert_eq!(r.to_row_string(), "TFUT");
        assert_eq!(r.selected().iter_ones().collect::<Vec<_>>(), vec![0, 3]);
    }

    #[test]
    fn not_equal_keeps_null_unknown() {
        let t = table();
        let idx = ColumnIndex::build(&t, 0, 1).unwrap();
        let r = idx.evaluate(CmpOp::Neq, &Scalar::Int(10)).unwrap();
        // row 0=10 F, row 1=20 T, row 2=NULL U, row 3=10 F.
        assert_eq!(r.to_row_string(), "FTUF");
    }

    #[test]
    fn type_mismatch_is_an_error_not_a_false_result() {
        let t = table();
        let idx = ColumnIndex::build(&t, 0, 1).unwrap();
        let err = idx.evaluate(CmpOp::Eq, &Scalar::Text("10".into()));
        assert!(matches!(err, Err(TviError::TypeMismatch { .. })));
    }
}
