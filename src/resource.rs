//! Resources and state: the in-memory relation catalog.
//!
//! All data is local synthetic fixture data supplied in the request (or loaded
//! from a bundled fixture file by the integration tests). There are no
//! external accounts or production data sources.

use std::collections::HashMap;
use std::sync::Arc;

use serde::Deserialize;
use serde_json::Value;

use crate::batch::{Batch, Column, DataType, Field, Scalar, Schema};
use crate::error::{QError, QResult};

/// On-the-wire schema for one relation fixture.
#[derive(Debug, Clone, Deserialize)]
pub struct RelationSpec {
    pub name: String,
    pub columns: Vec<ColumnSpec>,
    #[serde(default)]
    pub rows: Vec<Vec<Value>>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ColumnSpec {
    pub name: String,
    #[serde(rename = "type")]
    pub dtype: String,
}

/// A whole fixture document: a list of named relations.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct FixtureDoc {
    #[serde(default)]
    pub relations: Vec<RelationSpec>,
}

/// Immutable catalog of typed relation batches.
#[derive(Debug, Clone, Default)]
pub struct Catalog {
    relations: HashMap<String, Arc<Batch>>,
}

impl Catalog {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn insert(&mut self, name: impl Into<String>, batch: Arc<Batch>) {
        self.relations.insert(name.into(), batch);
    }

    pub fn get(&self, name: &str) -> QResult<Arc<Batch>> {
        self.limits_ok()?;
        self.relations
            .get(name)
            .cloned()
            .ok_or_else(|| QError::unknown(format!("relation '{name}' is not in the catalog")))
    }

    pub fn contains(&self, name: &str) -> bool {
        self.relations.contains_key(name)
    }

    pub fn names(&self) -> Vec<String> {
        let mut v: Vec<String> = self.relations.keys().cloned().collect();
        v.sort();
        v
    }

    /// Build a catalog from fixture specs, validating every literal.
    pub fn from_fixtures(specs: Vec<RelationSpec>) -> QResult<Self> {
        let mut cat = Catalog::new();
        for spec in specs {
            let batch = build_batch(&spec)?;
            if cat.contains(&spec.name) {
                return Err(QError::invalid_request(format!(
                    "relation '{}' is defined more than once in the fixture",
                    spec.name
                )));
            }
            cat.insert(spec.name, Arc::new(batch));
        }
        Ok(cat)
    }

    fn limits_ok(&self) -> QResult<()> {
        Ok(())
    }
}

fn parse_dtype(s: &str) -> QResult<DataType> {
    match s {
        "int" | "bigint" => Ok(DataType::Int),
        "str" | "varchar" | "text" => Ok(DataType::Str),
        other => Err(QError::invalid_request(format!(
            "unsupported column type '{other}' (allowed: int, str)"
        ))),
    }
}

fn build_batch(spec: &RelationSpec) -> QResult<Batch> {
    if spec.columns.is_empty() {
        return Err(QError::invalid_request(format!(
            "relation '{}' must declare at least one column",
            spec.name
        )));
    }
    let mut seen = std::collections::HashSet::new();
    for c in &spec.columns {
        if !seen.insert(&c.name) {
            return Err(QError::invalid_request(format!(
                "relation '{}' declares duplicate column '{}'",
                spec.name, c.name
            )));
        }
    }

    let fields: Vec<Field> = spec
        .columns
        .iter()
        .map(|c| Ok(Field::new(c.name.clone(), parse_dtype(&c.dtype)?)))
        .collect::<QResult<_>>()?;

    let ncols = spec.columns.len();
    let mut columns: Vec<Column> = (0..ncols)
        .map(|_| Vec::with_capacity(spec.rows.len()))
        .collect();

    for (ri, row) in spec.rows.iter().enumerate() {
        if row.len() != ncols {
            return Err(QError::invalid_request(format!(
                "relation '{}' row {ri} has {} values, expected {ncols}",
                spec.name,
                row.len()
            )));
        }
        for (ci, cell) in row.iter().enumerate() {
            let scalar = parse_scalar(cell, fields[ci].dtype, &spec.name, ri, &fields[ci].name)?;
            columns[ci].push(scalar);
        }
    }

    Batch::try_new(Arc::new(Schema::new(fields)), columns)
}

fn parse_scalar(
    cell: &Value,
    dtype: DataType,
    rel: &str,
    row: usize,
    col: &str,
) -> QResult<Scalar> {
    match cell {
        Value::Null => Ok(Scalar::Null),
        Value::Number(n) => match dtype {
            DataType::Int => {
                let i = n.as_i64().ok_or_else(|| {
                    QError::new(
                        crate::error::ErrorKind::InvalidLiteral,
                        format!("relation '{rel}' row {row} col '{col}': {n} is not an int64"),
                    )
                })?;
                Ok(Scalar::Int(i))
            }
            DataType::Str => Err(QError::new(
                crate::error::ErrorKind::InvalidLiteral,
                format!("relation '{rel}' row {row} col '{col}': number {n} not assignable to str"),
            )),
        },
        Value::String(s) => match dtype {
            DataType::Str => Ok(Scalar::Str(Arc::from(s.as_str()))),
            DataType::Int => Err(QError::new(
                crate::error::ErrorKind::InvalidLiteral,
                format!(
                    "relation '{rel}' row {row} col '{col}': string \"{s}\" not assignable to int"
                ),
            )),
        },
        other => Err(QError::new(
            crate::error::ErrorKind::InvalidLiteral,
            format!(
                "relation '{rel}' row {row} col '{col}': unsupported JSON cell {}",
                json_kind_name(other)
            ),
        )),
    }
}

fn json_kind_name(v: &Value) -> &'static str {
    match v {
        Value::Null => "null",
        Value::Bool(_) => "boolean",
        Value::Number(_) => "number",
        Value::String(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    }
}
