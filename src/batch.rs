//! Typed columnar batches.
//!
//! A [`Batch`] owns a [`Schema`] plus one column per field. Values are the
//! closed [`Scalar`] set (nullable `BIGINT` / `VARCHAR`). Batches can be
//! converted to Arrow2 record batches; the Arrow2 arrays are the boundary
//! representation used both for JSON serialization and for operator output.

use std::hash::{Hash, Hasher};
use std::sync::Arc;

use arrow2::array::{Array, PrimitiveArray, Utf8Array};
use arrow2::chunk::Chunk;
use arrow2::datatypes::{DataType as ArrowDataType, Field as ArrowField, Schema as ArrowSchema};
use serde::Serialize;

use crate::error::{QError, QResult};

/// Logical data types supported by the restricted fragment.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum DataType {
    /// Nullable 64-bit signed integer, maps to Arrow `Int64`.
    Int,
    /// Nullable UTF-8 string, maps to Arrow `Utf8`.
    Str,
}

impl DataType {
    pub fn arrow(self) -> ArrowDataType {
        match self {
            DataType::Int => ArrowDataType::Int64,
            DataType::Str => ArrowDataType::Utf8,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            DataType::Int => "int",
            DataType::Str => "str",
        }
    }
}

/// A single nullable value.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum Scalar {
    Null,
    Int(i64),
    /// Interned cheaply-cloneable string.
    Str(Arc<str>),
}

impl Scalar {
    pub fn dtype(&self) -> Option<DataType> {
        match self {
            Scalar::Null => None,
            Scalar::Int(_) => Some(DataType::Int),
            Scalar::Str(_) => Some(DataType::Str),
        }
    }

    pub fn is_null(&self) -> bool {
        matches!(self, Scalar::Null)
    }

    /// SQL comparison result with three-valued logic.
    ///
    /// * either side NULL -> `None` (UNKNOWN)
    /// * differing concrete types -> type error
    /// * otherwise the ordinary equality result
    pub fn sql_eq(&self, other: &Scalar) -> QResult<Option<bool>> {
        match (self, other) {
            (Scalar::Null, _) | (_, Scalar::Null) => Ok(None),
            (Scalar::Int(a), Scalar::Int(b)) => Ok(Some(a == b)),
            (Scalar::Str(a), Scalar::Str(b)) => Ok(Some(a == b)),
            _ => Err(QError::typemsg(format!(
                "cannot compare {} with {}",
                self.type_name(),
                other.type_name()
            ))),
        }
    }

    /// Key equality used by grouping / join hashing: NULL is not distinct
    /// from NULL (IS NOT DISTINCT FROM), unlike [`Scalar::sql_eq`].
    pub fn key_eq(&self, other: &Scalar) -> bool {
        self == other
    }

    fn type_name(&self) -> &'static str {
        match self {
            Scalar::Null => "null",
            Scalar::Int(_) => "int",
            Scalar::Str(_) => "str",
        }
    }
}

/// One named, typed field. All fields are nullable.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Field {
    pub name: Arc<str>,
    pub dtype: DataType,
}

impl Field {
    pub fn new(name: impl Into<Arc<str>>, dtype: DataType) -> Self {
        Self {
            name: name.into(),
            dtype,
        }
    }
}

/// Ordered list of fields with name lookup.
#[derive(Debug, Clone)]
pub struct Schema {
    pub fields: Vec<Field>,
}

impl Schema {
    pub fn new(fields: Vec<Field>) -> Self {
        Self { fields }
    }

    pub fn empty() -> Self {
        Self { fields: Vec::new() }
    }

    pub fn index_of(&self, name: &str) -> QResult<usize> {
        self.fields
            .iter()
            .position(|f| &*f.name == name)
            .ok_or_else(|| QError::unknown(format!("no column named '{name}'")))
    }

    pub fn dtype_of(&self, name: &str) -> QResult<DataType> {
        Ok(self.fields[self.index_of(name)?].dtype)
    }

    pub fn arrow_schema(self: &Arc<Self>) -> Arc<ArrowSchema> {
        let fields: Vec<ArrowField> = self
            .fields
            .iter()
            .map(|f| ArrowField::new(f.name.to_string(), f.dtype.arrow(), true))
            .collect();
        Arc::new(ArrowSchema::from(fields))
    }
}

/// A column is just the values in row order.
pub type Column = Vec<Scalar>;

/// A typed columnar batch: schema plus equal-length columns.
#[derive(Debug, Clone)]
pub struct Batch {
    schema: Arc<Schema>,
    columns: Vec<Column>,
    row_count: usize,
}

impl Batch {
    pub fn try_new(schema: Arc<Schema>, columns: Vec<Column>) -> QResult<Self> {
        if columns.len() != schema.fields.len() {
            return Err(QError::new(
                crate::error::ErrorKind::MalformedBatch,
                format!(
                    "schema has {} fields but batch has {} columns",
                    schema.fields.len(),
                    columns.len()
                ),
            ));
        }
        let row_count = columns.first().map_or(0, |c| c.len());
        for (c, f) in columns.iter().zip(schema.fields.iter()) {
            if c.len() != row_count {
                return Err(QError::new(
                    crate::error::ErrorKind::MalformedBatch,
                    format!(
                        "column '{}' has {} rows, expected {row_count}",
                        f.name,
                        c.len()
                    ),
                ));
            }
            for v in c {
                if let Some(dt) = v.dtype() {
                    if dt != f.dtype {
                        return Err(QError::new(
                            crate::error::ErrorKind::MalformedBatch,
                            format!(
                                "column '{}' declared {} but holds a {} value",
                                f.name,
                                f.dtype.name(),
                                dt.name()
                            ),
                        ));
                    }
                }
            }
        }
        Ok(Self {
            schema,
            columns,
            row_count,
        })
    }

    pub fn schema(&self) -> &Arc<Schema> {
        &self.schema
    }

    pub fn row_count(&self) -> usize {
        self.row_count
    }

    pub fn column(&self, idx: usize) -> &Column {
        &self.columns[idx]
    }

    pub fn get(&self, col: usize, row: usize) -> &Scalar {
        &self.columns[col][row]
    }

    /// Project a subset / reordering of columns by name.
    pub fn project(&self, names: &[String]) -> QResult<Batch> {
        let idxs: Vec<usize> = names
            .iter()
            .map(|n| self.schema.index_of(n))
            .collect::<QResult<_>>()?;
        let fields = idxs
            .iter()
            .map(|i| self.schema.fields[*i].clone())
            .collect();
        let columns = idxs.iter().map(|i| self.columns[*i].clone()).collect();
        Batch::try_new(Arc::new(Schema::new(fields)), columns)
    }

    /// Convert to an Arrow2 [`Chunk`] of boxed arrays.
    pub fn to_arrow(&self) -> Chunk<Box<dyn Array>> {
        let arrays: Vec<Box<dyn Array>> = self
            .schema
            .fields
            .iter()
            .zip(&self.columns)
            .map(|(f, col)| build_arrow_array(f.dtype, col))
            .collect();
        Chunk::new(arrays)
    }

    pub fn arrow_schema(&self) -> Arc<ArrowSchema> {
        self.schema.arrow_schema()
    }
}

fn build_arrow_array(dtype: DataType, col: &Column) -> Box<dyn Array> {
    match dtype {
        DataType::Int => {
            let vals: Vec<Option<i64>> = col
                .iter()
                .map(|v| match v {
                    Scalar::Int(i) => Some(*i),
                    _ => None,
                })
                .collect();
            // PrimitiveArray<i64> defaults to the Int64 physical type.
            Box::new(PrimitiveArray::<i64>::from(vals))
        }
        DataType::Str => {
            let vals: Vec<Option<&str>> = col
                .iter()
                .map(|v| match v {
                    Scalar::Str(s) => Some(&**s),
                    _ => None,
                })
                .collect();
            Box::new(Utf8Array::<i32>::from(vals))
        }
    }
}

/// Hashable borrowed view over a sequence of group/join key values.
/// Equality is IS NOT DISTINCT FROM (NULL == NULL on the key).
pub struct KeyRef<'a>(pub &'a [Scalar]);

impl<'a> PartialEq for KeyRef<'a> {
    fn eq(&self, other: &Self) -> bool {
        self.0.len() == other.0.len() && self.0.iter().zip(other.0.iter()).all(|(a, b)| a.key_eq(b))
    }
}

impl<'a> Eq for KeyRef<'a> {}

impl<'a> Hash for KeyRef<'a> {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.0.len().hash(state);
        for v in self.0 {
            match v {
                Scalar::Null => 0u8.hash(state),
                Scalar::Int(i) => {
                    1u8.hash(state);
                    i.hash(state);
                }
                Scalar::Str(s) => {
                    2u8.hash(state);
                    s.hash(state);
                }
            }
        }
    }
}

/// Owning counterpart of [`KeyRef`], for hash map storage.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct KeyBuf(pub Vec<Scalar>);

impl KeyBuf {
    pub fn as_ref(&self) -> KeyRef<'_> {
        KeyRef(&self.0)
    }
}
