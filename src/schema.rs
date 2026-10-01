//! Relation schema: ordered typed columns and their rows.
//!
//! A [`Relation`] is the logical, un-ordered multiset of rows. Ordering and
//! multiplicity aggregation are introduced deliberately when a Trie is built
//! (see [`crate::trie`]); keeping them out of the schema makes the distinction
//! between "data as supplied" and "access path" explicit.

use std::collections::HashSet;

use crate::error::{ErrorCode, Result, ServiceError};
use crate::value::{Cell, Scalar};

/// Supported column types.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ColumnType {
    Int,
    Str,
    Bool,
}

impl ColumnType {
    pub fn as_str(self) -> &'static str {
        match self {
            ColumnType::Int => "int",
            ColumnType::Str => "string",
            ColumnType::Bool => "bool",
        }
    }

    pub fn parse(s: &str) -> Result<Self> {
        match s {
            "int" | "integer" | "bigint" => Ok(ColumnType::Int),
            "string" | "str" | "text" => Ok(ColumnType::Str),
            "bool" | "boolean" => Ok(ColumnType::Bool),
            other => Err(ServiceError::new(
                ErrorCode::InvalidRequest,
                format!("unknown column type '{other}'"),
            )),
        }
    }

    fn accepts(self, value: &Scalar) -> bool {
        matches!(
            (self, value),
            (ColumnType::Int, Scalar::Int(_))
                | (ColumnType::Str, Scalar::Str(_))
                | (ColumnType::Bool, Scalar::Bool(_))
        )
    }
}

/// One typed, named column.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Column {
    pub name: String,
    pub typ: ColumnType,
}

impl Column {
    pub fn new(name: impl Into<String>, typ: ColumnType) -> Self {
        Self {
            name: name.into(),
            typ,
        }
    }
}

/// A named relation: schema plus row multiset (NULLs represented as
/// [`Cell::Null`]).
#[derive(Debug, Clone)]
pub struct Relation {
    pub name: String,
    pub columns: Vec<Column>,
    pub rows: Vec<Vec<Cell>>,
}

impl Relation {
    pub fn new(name: impl Into<String>, columns: Vec<Column>) -> Self {
        Self {
            name: name.into(),
            columns,
            rows: Vec::new(),
        }
    }

    pub fn with_rows(mut self, rows: Vec<Vec<Cell>>) -> Result<Self> {
        self.set_rows(rows)?;
        Ok(self)
    }

    pub fn set_rows(&mut self, rows: Vec<Vec<Cell>>) -> Result<()> {
        let arity = self.columns.len();
        for (i, row) in rows.iter().enumerate() {
            if row.len() != arity {
                return Err(ServiceError::new(
                    ErrorCode::InvalidRequest,
                    format!(
                        "relation '{}' row {i} has {} cells, schema has {arity} columns",
                        self.name,
                        row.len()
                    ),
                )
                .with_field(format!("relations.{}.rows", self.name)));
            }
            for (col, cell) in self.columns.iter().zip(row) {
                if let Cell::Value(v) = cell {
                    if !col.typ.accepts(v) {
                        return Err(ServiceError::new(
                            ErrorCode::TypeMismatch,
                            format!(
                                "relation '{}' row {i} column '{}' expected {}, got {}",
                                self.name,
                                col.name,
                                col.typ.as_str(),
                                v.type_name()
                            ),
                        )
                        .with_field(format!("relations.{}.rows", self.name)));
                    }
                }
            }
        }
        self.rows = rows;
        Ok(())
    }

    /// 0-based index of a column by name.
    pub fn column_index(&self, name: &str) -> Option<usize> {
        self.columns.iter().position(|c| c.name == name)
    }

    pub fn column_names(&self) -> Vec<String> {
        self.columns.iter().map(|c| c.name.clone()).collect()
    }

    /// Reject duplicate column names; used when ingesting a relation.
    pub fn check_unique_columns(&self) -> Result<()> {
        let mut seen = HashSet::new();
        for col in &self.columns {
            if !seen.insert(&col.name) {
                return Err(ServiceError::new(
                    ErrorCode::InvalidRequest,
                    format!("relation '{}' repeats column '{}'", self.name, col.name),
                ));
            }
        }
        Ok(())
    }
}
