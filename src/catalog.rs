//! Resources and state: the in-memory catalog of typed relations.
//!
//! The catalog is the only mutable service state. It is guarded by a simple
//! `Mutex`; fixtures are small and synthetic, and reads (query execution) take
//! the lock only long enough to clone `Arc`-backed batches, so queries never
//! block one another on data processing.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use crate::ast::{LoadFixturesRequest, RelationSpec};
use crate::batch::{Column, LogicalType, RecordBatch};
use crate::error::{EngineError, EngineResult};

#[derive(Debug, Clone)]
pub struct Relation {
    pub name: String,
    pub schema: Vec<(String, LogicalType)>,
    pub batch: Arc<RecordBatch>,
}

#[derive(Default)]
pub struct CatalogInner {
    relations: BTreeMap<String, Relation>,
}

#[derive(Clone, Default)]
pub struct Catalog {
    inner: Arc<Mutex<CatalogInner>>,
}

impl Catalog {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn load(&self, req: &LoadFixturesRequest, max_rows: usize) -> EngineResult<Vec<String>> {
        let mut guard = self.inner.lock().expect("catalog mutex poisoned");
        if req.replace {
            guard.relations.clear();
        }
        let mut loaded = Vec::new();
        for (name, spec) in &req.relations {
            if spec.rows.len() > max_rows {
                return Err(EngineError::validation(
                    format!(
                        "relation `{name}` has {} rows, exceeding max_rows_per_relation={max_rows}",
                        spec.rows.len()
                    ),
                    format!("relations.{name}.rows"),
                ));
            }
            let relation = build_relation(name.clone(), spec)?;
            guard.relations.insert(name.clone(), relation);
            loaded.push(name.clone());
        }
        Ok(loaded)
    }

    pub fn get(&self, name: &str) -> EngineResult<Relation> {
        let guard = self.inner.lock().expect("catalog mutex poisoned");
        guard
            .relations
            .get(name)
            .cloned()
            .ok_or_else(|| EngineError::schema(format!("relation `{name}` is not loaded")))
    }

    pub fn names(&self) -> Vec<String> {
        let guard = self.inner.lock().expect("catalog mutex poisoned");
        guard.relations.keys().cloned().collect()
    }
}

fn build_relation(name: String, spec: &RelationSpec) -> EngineResult<Relation> {
    if spec.columns.is_empty() {
        return Err(EngineError::validation(
            format!("relation `{name}` must declare at least one column"),
            format!("relations.{name}.columns"),
        ));
    }
    let mut seen = std::collections::BTreeSet::new();
    for c in &spec.columns {
        if !seen.insert(c.name.clone()) {
            return Err(EngineError::validation(
                format!("relation `{name}` declares duplicate column `{}`", c.name),
                format!("relations.{name}.columns"),
            ));
        }
    }

    let schema: Vec<(String, LogicalType)> = spec
        .columns
        .iter()
        .map(|c| LogicalType::parse(&c.r#type).map(|t| (c.name.clone(), t)))
        .collect::<EngineResult<Vec<_>>>()?;

    let mut columns: Vec<Column> = Vec::with_capacity(schema.len());
    for (col_name, ty) in &schema {
        let raw: Vec<serde_json::Value> = spec
            .rows
            .iter()
            .map(|row| {
                row.get(col_name)
                    .cloned()
                    .unwrap_or(serde_json::Value::Null)
            })
            .collect();
        columns.push(Column::from_options(col_name.clone(), *ty, &raw)?);
    }

    let batch = RecordBatch::new(columns)?;
    Ok(Relation {
        name,
        schema,
        batch: Arc::new(batch),
    })
}
