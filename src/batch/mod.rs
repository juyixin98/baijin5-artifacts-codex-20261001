//! Typed in-memory batches and their Arrow2 conversion.
//!
//! A [`TypedBatch`] is the engine's edge representation: typed [`Scalar`] rows
//! plus a declared schema. It converts deterministically to an Arrow2 `Chunk`,
//! which is serialized as an Arrow IPC stream for the API response.

pub mod value;

use std::collections::hash_map::DefaultHasher;
use std::hash::Hasher;

use arrow2::array::{
    Array, BooleanArray, ListArray, MutableArray, MutableListArray, MutablePrimitiveArray,
    MutableUtf8Array, PrimitiveArray, Utf8Array,
};
use arrow2::chunk::Chunk;
use arrow2::datatypes::{DataType as ArrowDataType, Field, Schema as ArrowSchema};

use crate::error::{EngineError, EngineResult};
use crate::plan::{ColumnDecl, ColumnType, Relation};

pub use value::{KeyRef, Scalar};

/// A typed relation instance.
#[derive(Debug, Clone)]
pub struct TypedBatch {
    columns: Vec<ColumnDecl>,
    rows: Vec<Vec<Scalar>>,
}

impl TypedBatch {
    /// Build from a wire relation, checking every value against its column.
    pub fn from_relation(rel: &Relation) -> EngineResult<Self> {
        let rows = rel
            .rows
            .iter()
            .enumerate()
            .map(|(row_idx, row)| {
                if row.len() != rel.columns.len() {
                    return Err(EngineError::invalid_data(format!(
                        "row {row_idx} has {} values, expected {}",
                        row.len(),
                        rel.columns.len()
                    )));
                }
                row.iter()
                    .zip(&rel.columns)
                    .map(|(v, col)| {
                        Scalar::from_json(v, col.data_type).map_err(|e| {
                            EngineError::invalid_data(format!(
                                "column '{}' row {row_idx}: {}",
                                col.name, e.message
                            ))
                        })
                    })
                    .collect::<EngineResult<Vec<_>>>()
            })
            .collect::<EngineResult<Vec<_>>>()?;
        Ok(Self {
            columns: rel.columns.clone(),
            rows,
        })
    }

    pub fn columns(&self) -> &[ColumnDecl] {
        &self.columns
    }

    pub fn rows(&self) -> &[Vec<Scalar>] {
        &self.rows
    }

    pub fn row_count(&self) -> usize {
        self.rows.len()
    }

    pub fn column_index(&self, name: &str) -> Option<usize> {
        self.columns.iter().position(|c| c.name == name)
    }

    pub fn column_type(&self, name: &str) -> Option<ColumnType> {
        self.columns
            .iter()
            .find(|c| c.name == name)
            .map(|c| c.data_type)
    }

    /// Append freshly produced rows (used by the executor's result sink).
    pub fn append_rows(&mut self, rows: impl IntoIterator<Item = Vec<Scalar>>) {
        self.rows.extend(rows);
    }

    /// Construct an empty typed batch with the given declared schema.
    pub fn empty(columns: Vec<crate::plan::ColumnDecl>) -> Self {
        Self {
            columns,
            rows: Vec::new(),
        }
    }

    /// Serialize to the wire relation model.
    pub fn to_relation(&self) -> Relation {
        Relation {
            columns: self.columns.clone(),
            rows: self
                .rows
                .iter()
                .map(|r| r.iter().map(Scalar::to_json).collect())
                .collect(),
        }
    }

    /// Fingerprint of one row restricted to the supplied column indices.
    ///
    /// Used for *set* dedup (`UNION`): the executor selects every column
    /// except the cycle marker, matching SQL `CYCLE` semantics (the marker is
    /// computed from, and therefore implied by, the rest of the row). It is a
    /// different mechanism from per-branch *path* membership, which compares
    /// the declared key against the path list only.
    pub fn row_fingerprint(row: &[Scalar], columns: &[usize]) -> u64 {
        let mut hasher = DefaultHasher::new();
        for &idx in columns {
            row[idx].fingerprint_component(&mut hasher);
        }
        hasher.finish()
    }

    /// Convert to an Arrow2 chunk, one array per declared column.
    pub fn to_arrow_chunk(&self) -> EngineResult<Chunk<Box<dyn Array>>> {
        let arrays = self
            .columns
            .iter()
            .enumerate()
            .map(|(col_idx, decl)| build_column(decl, &self.rows, col_idx))
            .collect::<EngineResult<Vec<_>>>()?;
        Ok(Chunk::new(arrays))
    }

    /// Arrow schema matching [`Self::to_arrow_chunk`].
    pub fn arrow_schema(&self) -> ArrowSchema {
        ArrowSchema::from(
            self.columns
                .iter()
                .map(|c| Field::new(c.name.clone(), to_arrow_type(c.data_type), true))
                .collect::<Vec<_>>(),
        )
    }
}

fn to_arrow_type(ty: ColumnType) -> ArrowDataType {
    match ty {
        ColumnType::Int64 => ArrowDataType::Int64,
        ColumnType::Utf8 => ArrowDataType::LargeUtf8,
        ColumnType::Bool => ArrowDataType::Boolean,
        ColumnType::ListInt64 => {
            ArrowDataType::LargeList(Box::new(Field::new("item", ArrowDataType::Int64, true)))
        }
        ColumnType::ListUtf8 => {
            ArrowDataType::LargeList(Box::new(Field::new("item", ArrowDataType::LargeUtf8, true)))
        }
    }
}

fn type_mismatch_err(decl: &ColumnDecl, other: &Scalar) -> EngineError {
    EngineError::internal(format!(
        "internal type mismatch building arrow column '{}' (declared {}): {other:?}",
        decl.name,
        decl.data_type.as_str()
    ))
}

fn arrow_err(e: arrow2::error::Error) -> EngineError {
    EngineError::internal(format!("arrow2 list build failed: {e}"))
}

fn build_column(
    decl: &ColumnDecl,
    rows: &[Vec<Scalar>],
    col_idx: usize,
) -> EngineResult<Box<dyn Array>> {
    match decl.data_type {
        ColumnType::Int64 => {
            let mut b = MutablePrimitiveArray::<i64>::new();
            for row in rows {
                match &row[col_idx] {
                    Scalar::Int(v) => b.push(Some(*v)),
                    Scalar::Null => b.push(None),
                    other => return Err(type_mismatch_err(decl, other)),
                }
            }
            let arr: PrimitiveArray<i64> = b.into();
            Ok(arr.boxed())
        }
        ColumnType::Bool => {
            let mut b = arrow2::array::MutableBooleanArray::new();
            for row in rows {
                match &row[col_idx] {
                    Scalar::Bool(v) => b.push(Some(*v)),
                    Scalar::Null => b.push(None),
                    other => return Err(type_mismatch_err(decl, other)),
                }
            }
            let arr: BooleanArray = b.into();
            Ok(arr.boxed())
        }
        ColumnType::Utf8 => {
            let mut b = MutableUtf8Array::<i64>::new();
            for row in rows {
                match &row[col_idx] {
                    Scalar::Utf8(v) => b.push(Some(v.as_str())),
                    Scalar::Null => b.push::<&str>(None),
                    other => return Err(type_mismatch_err(decl, other)),
                }
            }
            let arr: Utf8Array<i64> = b.into();
            Ok(arr.boxed())
        }
        ColumnType::ListInt64 => {
            let mut b = MutableListArray::<i64, MutablePrimitiveArray<i64>>::new();
            for row in rows {
                match &row[col_idx] {
                    Scalar::IntList(xs) => {
                        for x in xs {
                            b.mut_values().push(Some(*x));
                        }
                        b.try_push_valid().map_err(arrow_err)?;
                    }
                    Scalar::Null => b.push_null(),
                    other => return Err(type_mismatch_err(decl, other)),
                }
            }
            Ok(ListArray::from(b).boxed())
        }
        ColumnType::ListUtf8 => {
            let mut b = MutableListArray::<i64, MutableUtf8Array<i64>>::new();
            for row in rows {
                match &row[col_idx] {
                    Scalar::Utf8List(xs) => {
                        for x in xs {
                            b.mut_values().push(Some(x.as_str()));
                        }
                        b.try_push_valid().map_err(arrow_err)?;
                    }
                    Scalar::Null => b.push_null(),
                    other => return Err(type_mismatch_err(decl, other)),
                }
            }
            Ok(ListArray::from(b).boxed())
        }
    }
}

#[cfg(test)]
mod tests_unit;
