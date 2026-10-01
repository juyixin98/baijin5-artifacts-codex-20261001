//! Typed columnar batches.
//!
//! A [`Batch`] is a [`arrow2::chunk::Chunk`] plus the logical [`RelationSchema`]
//! describing it. Construction validates that columns match the schema in
//! count, type and row count — an invalid batch can never reach the operator.

use std::sync::Arc;

use arrow2::array::{Array, BooleanArray, Float64Array, Int64Array, Utf8Array};
use arrow2::chunk::Chunk;
use serde::{Deserialize, Serialize};

use crate::error::{Result, SlError};
use crate::schema::{ColumnType, RelationSchema};
use crate::scalar::Scalar;

/// A column expressed as plain Rust values. Used by fixtures and tests (and by
/// request decoding) to build a typed batch without touching arrow2 directly.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", content = "values", rename_all = "snake_case")]
pub enum Column {
    Int64(Vec<Option<i64>>),
    Float64(Vec<Option<f64>>),
    Utf8(Vec<Option<String>>),
    Boolean(Vec<Option<bool>>),
}

impl Column {
    pub fn len(&self) -> usize {
        match self {
            Column::Int64(v) => v.len(),
            Column::Float64(v) => v.len(),
            Column::Utf8(v) => v.len(),
            Column::Boolean(v) => v.len(),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Materialize into the corresponding arrow2 array.
    pub fn to_arrow_array(&self) -> Box<dyn Array> {
        match self {
            Column::Int64(v) => Box::new(Int64Array::from(v.as_slice())),
            Column::Float64(v) => Box::new(Float64Array::from(v.as_slice())),
            Column::Utf8(v) => Box::new(Utf8Array::<i32>::from_iter(
                v.iter().map(|x| x.as_deref()),
            )),
            Column::Boolean(v) => Box::new(BooleanArray::from_iter(v.iter().copied())),
        }
    }
}

/// A typed, validated columnar batch. Cheaply cloneable: the schema is shared
/// and the arrow arrays are reference counted internally.
#[derive(Debug, Clone)]
pub struct Batch {
    schema: Arc<RelationSchema>,
    chunk: Chunk<Box<dyn Array>>,
}

impl Batch {
    /// Build a batch from plain value columns.
    ///
    /// Errors with [`crate::error::ErrorCategory::Validation`] when column count
    /// or types do not match the schema, or when columns differ in length.
    pub fn try_from_columns(schema: Arc<RelationSchema>, columns: Vec<Column>) -> Result<Self> {
        if columns.len() != schema.len() {
            return Err(SlError::validation(
                "column_count_mismatch",
                format!(
                    "schema declares {} columns but batch carries {}",
                    schema.len(),
                    columns.len()
                ),
            ));
        }
        let expected: Vec<_> = schema.columns.iter().map(|c| c.ty).collect();
        let actual: Vec<_> = columns.iter().map(|c| ColumnType::from(ColumnTy::of(c))).collect();
        if expected != actual {
            return Err(SlError::validation(
                "column_type_mismatch",
                format!("schema types {expected:?} do not match batch types {actual:?}"),
            ));
        }
        let row_count = columns.first().map_or(0, Column::len);
        if columns.iter().any(|c| c.len() != row_count) {
            return Err(SlError::validation(
                "ragged_batch",
                "columns within a batch must have equal lengths",
            ));
        }
        let arrays = columns.iter().map(Column::to_arrow_array).collect();
        // Chunk::try_new re-checks equal lengths as defense in depth.
        let chunk = Chunk::try_new(arrays)?;
        Ok(Self { schema, chunk })
    }

    /// Wrap an arrow2 chunk, checking its physical types against the schema.
    pub fn try_from_chunk(
        schema: Arc<RelationSchema>,
        chunk: Chunk<Box<dyn Array>>,
    ) -> Result<Self> {
        if chunk.arrays().len() != schema.len() {
            return Err(SlError::validation(
                "column_count_mismatch",
                format!(
                    "schema declares {} columns but arrow chunk carries {}",
                    schema.len(),
                    chunk.arrays().len()
                ),
            ));
        }
        for (i, (field, arr)) in schema
            .columns
            .iter()
            .zip(chunk.arrays().iter())
            .enumerate()
        {
            let physical = crate::schema::ColumnType::from_arrow(arr.data_type());
            match physical {
                Ok(ty) if ty == field.ty => {}
                _ => {
                    return Err(SlError::validation(
                        "column_type_mismatch",
                        format!(
                            "column {} ({}) expected {:?} but chunk has {:?}",
                            i,
                            field.name,
                            field.ty,
                            arr.data_type()
                        ),
                    ));
                }
            }
        }
        Ok(Self { schema, chunk })
    }

    pub fn schema(&self) -> &Arc<RelationSchema> {
        &self.schema
    }

    pub fn num_rows(&self) -> usize {
        self.chunk.len()
    }

    pub fn num_columns(&self) -> usize {
        self.chunk.arrays().len()
    }

    pub fn columns(&self) -> &[Box<dyn Array>] {
        self.chunk.arrays()
    }

    pub fn arrow_chunk(&self) -> &Chunk<Box<dyn Array>> {
        &self.chunk
    }

    /// Replace the chunk (used after IPC decode); schema stays shared.
    pub(crate) fn with_chunk(self, chunk: Chunk<Box<dyn Array>>) -> Self {
        Self {
            schema: self.schema,
            chunk,
        }
    }

    /// Build a batch from already-owned rows (one `Vec<Scalar>` per row, in
    /// schema column order). Used to rebuild final results and to decode
    /// spilled runs. A `Scalar::Null` in a non-nullable position is accepted
    /// at this layer (nullable is a source contract, not an ordering rule).
    pub fn from_rows(schema: Arc<RelationSchema>, rows: &[Vec<Scalar>]) -> Result<Self> {
        let ncols = schema.len();
        for (ri, row) in rows.iter().enumerate() {
            if row.len() != ncols {
                return Err(SlError::validation(
                    "row_width_mismatch",
                    format!(
                        "row {ri} has {} values but schema declares {ncols}",
                        row.len()
                    ),
                ));
            }
        }
        let mut columns: Vec<Vec<Scalar>> = (0..ncols).map(|_| Vec::with_capacity(rows.len())).collect();
        for row in rows {
            for (ci, scalar) in row.iter().enumerate() {
                columns[ci].push(scalar.clone());
            }
        }
        let typed = schema
            .columns
            .iter()
            .zip(columns)
            .map(|(col, scalars)| scalars_to_column(col.ty, scalars))
            .collect();
        Self::try_from_columns(schema, typed)
    }
}

/// Convert an owned scalar column into the plain [`Column`] representation.
pub(crate) fn scalars_to_column(ty: ColumnType, scalars: Vec<Scalar>) -> Column {
    match ty {
        ColumnType::Int64 => Column::Int64(
            scalars
                .into_iter()
                .map(|s| match s {
                    Scalar::I64(v) => Some(v),
                    _ => None,
                })
                .collect(),
        ),
        ColumnType::Float64 => Column::Float64(
            scalars
                .into_iter()
                .map(|s| match s {
                    Scalar::F64(v) => Some(v),
                    _ => None,
                })
                .collect(),
        ),
        ColumnType::Utf8 => Column::Utf8(
            scalars
                .into_iter()
                .map(|s| match s {
                    Scalar::Utf8(v) => Some(v),
                    _ => None,
                })
                .collect(),
        ),
        ColumnType::Boolean => Column::Boolean(
            scalars
                .into_iter()
                .map(|s| match s {
                    Scalar::Boolean(v) => Some(v),
                    _ => None,
                })
                .collect(),
        ),
    }
}

/// Transpose rows (each in schema column order) into plain [`Column`]s.
/// Values whose variant does not match the column type are rendered as NULL.
pub fn rows_to_columns(schema: &RelationSchema, rows: &[Vec<Scalar>]) -> Vec<Column> {
    let ncols = schema.len();
    let mut columns: Vec<Vec<Scalar>> =
        (0..ncols).map(|_| Vec::with_capacity(rows.len())).collect();
    for row in rows {
        for (ci, scalar) in row.iter().take(ncols).enumerate() {
            columns[ci].push(scalar.clone());
        }
    }
    schema
        .columns
        .iter()
        .zip(columns)
        .map(|(col, scalars)| scalars_to_column(col.ty, scalars))
        .collect()
}

/// Private discriminant so we can compare a [`Column`]'s type to schema without
/// cloning its payload.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ColumnTy {
    Int64,
    Float64,
    Utf8,
    Boolean,
}

impl ColumnTy {
    fn of(c: &Column) -> Self {
        match c {
            Column::Int64(_) => ColumnTy::Int64,
            Column::Float64(_) => ColumnTy::Float64,
            Column::Utf8(_) => ColumnTy::Utf8,
            Column::Boolean(_) => ColumnTy::Boolean,
        }
    }
}

impl From<ColumnTy> for crate::schema::ColumnType {
    fn from(value: ColumnTy) -> Self {
        match value {
            ColumnTy::Int64 => crate::schema::ColumnType::Int64,
            ColumnTy::Float64 => crate::schema::ColumnType::Float64,
            ColumnTy::Utf8 => crate::schema::ColumnType::Utf8,
            ColumnTy::Boolean => crate::schema::ColumnType::Boolean,
        }
    }
}
