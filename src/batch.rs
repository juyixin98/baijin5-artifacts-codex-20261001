//! Typed columnar batches built on arrow2.
//!
//! The join core is intentionally generic over *native* columns
//! ([`Column`]), while [`Batch`] is the arrow2-facing envelope used at
//! system boundaries (JSON / HTTP). Supported physical key type is
//! signed 64-bit integer; every value slot may be NULL, and NULL never
//! matches any comparison (SQL three-valued logic).
//!
//! Each row also carries a stable, caller-supplied `row_id` (string).
//! Row ids let us distinguish physically identical duplicate rows: the
//! join preserves both their identity and their Cartesian multiplicity.

use std::collections::BTreeMap;
use std::sync::Arc;

use arrow2::array::{Array, Int64Array, PrimitiveArray, Utf8Array};
use arrow2::chunk::Chunk;
use arrow2::datatypes::{DataType, Field, Schema};

use crate::error::{JoinError, JoinResult};

/// Native nullable key column. `None` == SQL NULL.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Column {
    pub name: String,
    pub values: Vec<Option<i64>>,
}

impl Column {
    #[must_use]
    pub fn new(name: impl Into<String>, values: Vec<Option<i64>>) -> Self {
        Self {
            name: name.into(),
            values,
        }
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.values.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.values.is_empty()
    }
}

/// One input relation: an ordered set of equal-length columns plus a
/// stable identifier per row.
#[derive(Clone, Debug)]
pub struct Batch {
    pub columns: Vec<Column>,
    /// `row_ids.len() == column row count`; duplicates are kept.
    pub row_ids: Vec<String>,
}

impl Batch {
    /// Construct from columns, validating equal lengths. Row ids default
    /// to the numeric position (`"0"`, `"1"`, ...); use
    /// [`Batch::with_row_ids`] to preserve duplicate identity.
    pub fn new(columns: Vec<Column>) -> JoinResult<Self> {
        let n = columns.first().map_or(0, |c| c.values.len());
        for c in &columns {
            if c.values.len() != n {
                return Err(JoinError::input(
                    "column_length_mismatch",
                    format!(
                        "column '{}' has {} rows, expected {}",
                        c.name,
                        c.values.len(),
                        n
                    ),
                ));
            }
        }
        let row_ids = (0..n).map(|i| i.to_string()).collect();
        Ok(Self { columns, row_ids })
    }

    #[must_use]
    pub fn with_row_ids(mut self, row_ids: Vec<String>) -> Self {
        self.row_ids = row_ids;
        self
    }

    #[must_use]
    pub fn row_count(&self) -> usize {
        self.row_ids.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.row_ids.is_empty()
    }

    /// Look up a column by name (first match wins).
    #[must_use]
    pub fn column(&self, name: &str) -> Option<&Column> {
        self.columns.iter().find(|c| c.name == name)
    }

    /// Validate row ids are present and unique within the batch.
    /// Duplicate *values* are always allowed; this checks identity only.
    pub fn validate_unique_row_ids(&self) -> JoinResult<()> {
        if self.row_ids.len() != self.row_count() {
            return Err(JoinError::input(
                "row_id_count_mismatch",
                format!(
                    "{} rows but {} row ids",
                    self.row_count(),
                    self.row_ids.len()
                ),
            ));
        }
        let mut seen = std::collections::HashSet::new();
        for id in &self.row_ids {
            if !seen.insert(id.as_str()) {
                return Err(JoinError::input(
                    "duplicate_row_id",
                    format!("duplicate row id '{id}' within one batch"),
                ));
            }
        }
        Ok(())
    }

    /// Project key columns into a cheap physical descriptor consumed by
    /// the operator (names + raw nullable slices).
    ///
    /// # Errors
    /// Fails with `Input` when a referenced key column is absent.
    pub fn require_columns(&self, names: &[String]) -> JoinResult<Vec<&Column>> {
        names
            .iter()
            .map(|n| {
                self.column(n).ok_or_else(|| {
                    JoinError::input("unknown_column", format!("column '{n}' not found in batch"))
                })
            })
            .collect()
    }
}

// ---------------------------------------------------------------------
// arrow2 conversion: Batch -> Chunk<Box<dyn Array>>
// ---------------------------------------------------------------------

/// arrow2 schema for a batch: row_id: Utf8, one Int64 column per [`Column`].
#[must_use]
pub fn arrow_schema(batch: &Batch) -> Schema {
    let mut fields = Vec::with_capacity(batch.columns.len() + 1);
    fields.push(Field::new("row_id", DataType::Utf8, false));
    for c in &batch.columns {
        fields.push(Field::new(c.name.clone(), DataType::Int64, true));
    }
    Schema::from(fields)
}

/// Convert a native batch into an arrow2 [`Chunk`].
#[must_use]
pub fn to_arrow_chunk(batch: &Batch) -> Chunk<Box<dyn Array>> {
    let id_values: Vec<&str> = batch.row_ids.iter().map(String::as_str).collect();
    let id_arr: Utf8Array<i32> = Utf8Array::<i32>::from_slice(id_values);
    let mut arrays: Vec<Box<dyn Array>> = Vec::with_capacity(batch.columns.len() + 1);
    arrays.push(Box::new(id_arr));
    for c in &batch.columns {
        let arr: Int64Array = PrimitiveArray::from(c.values.clone());
        arrays.push(Box::new(arr));
    }
    Chunk::new(arrays)
}

/// Reference-counted arrow chunk plus its schema, suitable for batched
/// transfer / IPC.
#[derive(Clone)]
pub struct ArrowTable {
    pub schema: Arc<Schema>,
    pub chunks: Vec<Chunk<Box<dyn Array>>>,
}

impl ArrowTable {
    #[must_use]
    pub fn from_batch(batch: &Batch) -> Self {
        Self {
            schema: Arc::new(arrow_schema(batch)),
            chunks: vec![to_arrow_chunk(batch)],
        }
    }

    #[must_use]
    pub fn num_rows(&self) -> usize {
        self.chunks.iter().map(Chunk::len).sum()
    }
}

/// Snapshot of a join output pair, used for JSON/debug rendering and
/// multiset comparison in tests.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct OutputPair {
    pub left_id: String,
    pub right_id: String,
}

/// Multiset helper: counts identical `(left_id, right_id)` pairs.
#[must_use]
pub fn multiset(pairs: &[OutputPair]) -> BTreeMap<(String, String), usize> {
    let mut m = BTreeMap::new();
    for p in pairs {
        *m.entry((p.left_id.clone(), p.right_id.clone()))
            .or_default() += 1;
    }
    m
}
