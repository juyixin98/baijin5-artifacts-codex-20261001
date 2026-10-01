//! Column-value bitmap indexes.
//!
//! For each indexed column we keep:
//! * a NULL bitmap (rows where the column value is SQL NULL);
//! * a dictionary of value -> bitmap for equality probes (`=` / `<>`);
//! * the typed values for ordered scans (`<`, `<=`, `>`, `>=`).
//!
//! All index bitmaps cover the FULL append universe. Predicate results are
//! [`TriSet`]s restricted to the caller-supplied ALIVE universe (versioned
//! deletes live in [`crate::state::TableState`], not in the index), so a deleted
//! row can never match. Combining predicates is only possible when they were
//! evaluated against the same alive set — enforced by [`TriSet`].

use std::collections::HashMap;

use serde::{Deserialize, Serialize};

use crate::batch::{ColumnData, ColumnType};
use crate::bits::Bitmap;
use crate::error::{Error, ErrorKind, Result};
use crate::logic::TriSet;

/// Scalar comparison literal from a query.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum Literal {
    Int(i64),
    Text(String),
    Bool(bool),
}

impl Literal {
    fn type_name(&self) -> &'static str {
        match self {
            Literal::Int(_) => "int",
            Literal::Text(_) => "text",
            Literal::Bool(_) => "bool",
        }
    }
}

/// Predicates supported on a single column.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CmpOp {
    Eq,
    Neq,
    Lt,
    Lte,
    Gt,
    Gte,
    IsNull,
    IsNotNull,
}

impl CmpOp {
    pub fn parse(s: &str) -> Result<Self> {
        Ok(match s {
            "=" | "eq" => CmpOp::Eq,
            "!=" | "<>" | "neq" => CmpOp::Neq,
            "<" | "lt" => CmpOp::Lt,
            "<=" | "lte" => CmpOp::Lte,
            ">" | "gt" => CmpOp::Gt,
            ">=" | "gte" => CmpOp::Gte,
            "is null" | "is_null" => CmpOp::IsNull,
            "is not null" | "is_not_null" => CmpOp::IsNotNull,
            other => {
                return Err(Error::new(
                    ErrorKind::UnsupportedOperator,
                    format!("unknown comparison operator '{other}'"),
                ))
            }
        })
    }

    pub fn needs_literal(self) -> bool {
        !matches!(self, CmpOp::IsNull | CmpOp::IsNotNull)
    }
}

/// Bitmap dictionary for one column, over the full append universe.
#[derive(Debug, Clone)]
pub struct ColumnIndex {
    pub name: String,
    pub col_type: ColumnType,
    len: usize,
    nulls: Bitmap,
    int_values: Option<HashMap<i64, Bitmap>>,
    text_values: Option<HashMap<String, Bitmap>>,
    bool_true: Option<Bitmap>,
    data: ColumnData,
}

impl ColumnIndex {
    pub fn build(name: impl Into<String>, data: ColumnData) -> Self {
        let name = name.into();
        let col_type = data.col_type();
        let len = data.len();
        let nulls = pack_nulls(len, &data);

        let mut idx = ColumnIndex {
            name,
            col_type,
            len,
            nulls,
            int_values: None,
            text_values: None,
            bool_true: None,
            data,
        };
        idx.build_dictionaries();
        idx
    }

    fn build_dictionaries(&mut self) {
        match &self.data {
            ColumnData::Int(v) => {
                let mut map: HashMap<i64, Bitmap> = HashMap::new();
                for (i, x) in v.iter().enumerate() {
                    if let Some(n) = x {
                        map.entry(*n)
                            .or_insert_with(|| Bitmap::zeros(self.len))
                            .set(i, true);
                    }
                }
                self.int_values = Some(map);
            }
            ColumnData::Text(v) => {
                let mut map: HashMap<String, Bitmap> = HashMap::new();
                for (i, x) in v.iter().enumerate() {
                    if let Some(s) = x {
                        map.entry(s.clone())
                            .or_insert_with(|| Bitmap::zeros(self.len))
                            .set(i, true);
                    }
                }
                self.text_values = Some(map);
            }
            ColumnData::Bool(v) => {
                let mut trues = Bitmap::zeros(self.len);
                for (i, x) in v.iter().enumerate() {
                    if matches!(x, Some(true)) {
                        trues.set(i, true);
                    }
                }
                self.bool_true = Some(trues);
            }
        }
    }

    pub fn len(&self) -> usize {
        self.len
    }

    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    pub fn null_bitmap(&self) -> &Bitmap {
        &self.nulls
    }

    /// Rows whose non-null value equals `lit` (bitmap lookup).
    fn equality_bitmap(&self, lit: &Literal) -> Result<Bitmap> {
        match (self.col_type, lit) {
            (ColumnType::Int, Literal::Int(n)) => Ok(self
                .int_values
                .as_ref()
                .and_then(|m| m.get(n))
                .cloned()
                .unwrap_or_else(|| Bitmap::zeros(self.len))),
            (ColumnType::Text, Literal::Text(s)) => Ok(self
                .text_values
                .as_ref()
                .and_then(|m| m.get(s))
                .cloned()
                .unwrap_or_else(|| Bitmap::zeros(self.len))),
            (ColumnType::Bool, Literal::Bool(b)) => {
                let trues = self.bool_true.as_ref().expect("bool index built");
                Ok(if *b {
                    trues.clone()
                } else {
                    // non-null FALSE = non-null rows minus TRUE rows
                    let non_null = self.non_null_bitmap();
                    non_null.and_not(trues).expect("same universe")
                })
            }
            (ct, lit) => Err(Error::new(
                ErrorKind::TypeMismatch,
                format!(
                    "column '{}' is {} but compared against {} literal",
                    self.name,
                    ct.as_str(),
                    lit.type_name()
                ),
            )),
        }
    }

    fn non_null_bitmap(&self) -> Bitmap {
        // Full universe minus nulls (index universe has no deletions concept;
        // alive restriction happens at TriSet construction).
        let full = Bitmap::ones(self.len);
        full.and_not(&self.nulls).expect("same universe")
    }

    /// Ordered comparison via a typed scan.
    fn order_bitmap(&self, op: CmpOp, lit: &Literal) -> Result<Bitmap> {
        let mut out = Bitmap::zeros(self.len);
        match (&self.data, lit) {
            (ColumnData::Int(v), Literal::Int(target)) => {
                for (i, val) in v.iter().enumerate() {
                    if let Some(val) = val {
                        out.set(
                            i,
                            match op {
                                CmpOp::Lt => val < target,
                                CmpOp::Lte => val <= target,
                                CmpOp::Gt => val > target,
                                CmpOp::Gte => val >= target,
                                _ => unreachable!("order_bitmap called with {op:?}"),
                            },
                        );
                    }
                }
            }
            (ColumnData::Text(v), Literal::Text(s)) => {
                for (i, val) in v.iter().enumerate() {
                    if let Some(val) = val {
                        out.set(
                            i,
                            match op {
                                CmpOp::Lt => val < s,
                                CmpOp::Lte => val <= s,
                                CmpOp::Gt => val > s,
                                CmpOp::Gte => val >= s,
                                _ => unreachable!("order_bitmap called with {op:?}"),
                            },
                        );
                    }
                }
            }
            (ColumnData::Bool(_), _) => {
                return Err(Error::new(
                    ErrorKind::UnsupportedOperator,
                    format!(
                        "ordered comparison not supported on bool column '{}'",
                        self.name
                    ),
                ));
            }
            (_, lit) => {
                return Err(Error::new(
                    ErrorKind::TypeMismatch,
                    format!(
                        "column '{}' is {} but range-compared against {} literal",
                        self.name,
                        self.col_type.as_str(),
                        lit.type_name()
                    ),
                ));
            }
        }
        Ok(out)
    }

    /// Evaluate a column predicate into a [`TriSet`] over `alive`.
    ///
    /// Semantics:
    /// * NULL operand  -> UNKNOWN for every comparison except IS [NOT] NULL;
    /// * IS NULL       -> NULL rows TRUE, other alive rows FALSE;
    /// * otherwise     -> per-row boolean result for non-NULL rows.
    pub fn evaluate(&self, op: CmpOp, lit: Option<&Literal>, alive: &Bitmap) -> Result<TriSet> {
        if alive.len() != self.len {
            return Err(Error::new(
                ErrorKind::UniverseMismatch,
                format!(
                    "index '{}' covers {} rows but alive universe has {}",
                    self.name,
                    self.len,
                    alive.len()
                ),
            ));
        }
        match op {
            CmpOp::IsNull => return TriSet::is_null(alive, &self.nulls),
            CmpOp::IsNotNull => {
                let inner = TriSet::is_null(alive, &self.nulls)?;
                return inner.negate();
            }
            _ => {}
        }
        let lit = lit.ok_or_else(|| {
            Error::invalid(format!(
                "operator {:?} on '{}' requires a literal",
                op, self.name
            ))
        })?;

        let matches = match op {
            CmpOp::Eq => self.equality_bitmap(lit)?,
            CmpOp::Neq => {
                let eq = self.equality_bitmap(lit)?;
                // Non-null rows that do NOT equal: non_null − eq.
                self.non_null_bitmap().and_not(&eq)?
            }
            CmpOp::Lt | CmpOp::Lte | CmpOp::Gt | CmpOp::Gte => self.order_bitmap(op, lit)?,
            CmpOp::IsNull | CmpOp::IsNotNull => unreachable!(),
        };

        // TRUE = matches ∩ alive ; FALSE = (non-null − matches) ∩ alive ;
        // UNKNOWN = nulls ∩ alive. Deleted rows land in none of them.
        let non_null = self.non_null_bitmap();
        let t = matches.and(alive)?;
        let f = non_null.and_not(&matches)?.and(alive)?;
        TriSet::from_tf(alive.clone(), t, f)
    }
}

/// Pack the per-row null flags into a tail-cleared bitmap.
fn pack_nulls(len: usize, data: &ColumnData) -> Bitmap {
    let flags = data.null_mask();
    let mut bm = Bitmap::zeros(len);
    for (i, is_null) in flags.into_iter().enumerate() {
        bm.set(i, is_null);
    }
    bm
}

/// A table's collection of per-column indexes, all over one shared universe.
#[derive(Debug, Clone)]
pub struct TableIndexes {
    pub columns: HashMap<String, ColumnIndex>,
}

impl TableIndexes {
    pub fn from_columns(columns: Vec<(String, ColumnData)>) -> Self {
        let mut map = HashMap::new();
        for (name, data) in columns {
            map.insert(name.clone(), ColumnIndex::build(name, data));
        }
        Self { columns: map }
    }

    pub fn get(&self, column: &str) -> Result<&ColumnIndex> {
        self.columns.get(column).ok_or_else(|| {
            Error::new(
                ErrorKind::NotFound,
                format!("no index for column '{column}'"),
            )
        })
    }
}
