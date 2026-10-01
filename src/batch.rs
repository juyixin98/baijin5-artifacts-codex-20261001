//! Typed columnar batches: the unit data enters and leaves the backend.
//!
//! A [`TypedBatch`] owns an ordered, named schema and column-major data. The
//! join engine never sees untyped JSON: rows are validated against the schema
//! at the boundary. NULL is represented by [`Datum::Null`].
use std::sync::Arc;

use serde::{Deserialize, Serialize};

use crate::domain::{Datum, LogicalType};
use crate::error::{ErrorCode, JoinError, JoinResult};

/// One named, typed column.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ColumnSchema {
    pub name: String,
    #[serde(rename = "type")]
    pub ty: LogicalType,
}

impl ColumnSchema {
    pub fn new(name: impl Into<String>, ty: LogicalType) -> Self {
        Self {
            name: name.into(),
            ty,
        }
    }
}

/// Ordered list of columns; column position is identity within a relation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RelationSchema {
    pub columns: Vec<ColumnSchema>,
}

impl RelationSchema {
    pub fn new(columns: Vec<ColumnSchema>) -> Self {
        Self { columns }
    }

    pub fn arity(&self) -> usize {
        self.columns.len()
    }

    pub fn index_of(&self, name: &str) -> Option<usize> {
        self.columns.iter().position(|c| c.name == name)
    }
}

/// Column-major typed data. `columns.len() == schema.arity()` and every column
/// has the same length.
#[derive(Debug, Clone)]
pub struct TypedBatch {
    schema: Arc<RelationSchema>,
    columns: Vec<Vec<Datum>>,
    /// Number of logical rows (all columns share this length).
    row_count: usize,
}

impl TypedBatch {
    /// Build from row-major input, validating arity and each datum's type
    /// against the schema.
    pub fn from_rows(schema: Arc<RelationSchema>, rows: Vec<Vec<Datum>>) -> JoinResult<Self> {
        if schema.arity() == 0 {
            return Err(JoinError::new(
                ErrorCode::EmptySchema,
                "relation schema must declare at least one column",
            ));
        }
        let mut columns: Vec<Vec<Datum>> = (0..schema.arity())
            .map(|_| Vec::with_capacity(rows.len()))
            .collect();
        for (ri, row) in rows.into_iter().enumerate() {
            if row.len() != schema.arity() {
                return Err(JoinError::new(
                    ErrorCode::RowArityMismatch,
                    format!(
                        "row {ri} has {} fields but schema declares {}",
                        row.len(),
                        schema.arity()
                    ),
                ));
            }
            for (ci, datum) in row.into_iter().enumerate() {
                if let Some(actual) = datum.logical_type() {
                    let expected = schema.columns[ci].ty;
                    if actual != expected {
                        return Err(JoinError::new(
                            ErrorCode::TypeMismatch,
                            format!(
                                "row {ri} column '{}' expected {} but got {}",
                                schema.columns[ci].name,
                                expected.as_str(),
                                actual.as_str()
                            ),
                        ));
                    }
                }
                columns[ci].push(datum);
            }
        }
        let row_count = columns[0].len();
        Ok(Self {
            schema,
            columns,
            row_count,
        })
    }

    pub fn schema(&self) -> &Arc<RelationSchema> {
        &self.schema
    }

    pub fn row_count(&self) -> usize {
        self.row_count
    }

    pub fn column(&self, index: usize) -> &[Datum] {
        &self.columns[index]
    }

    /// One row projected onto `indices`, e.g. a trie column order.
    pub fn projected_row(&self, row: usize, indices: &[usize]) -> Vec<Datum> {
        indices
            .iter()
            .map(|&c| self.columns[c][row].clone())
            .collect()
    }
}
