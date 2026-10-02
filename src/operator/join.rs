//! Equi-join operator between frontier rows of the recursive view and the
//! edge relation.
//!
//! In-memory hash join: the edge side is indexed on the declared edge join
//! columns. Ordering is fully deterministic — frontier rows are processed in
//! stored order and matches come out in edge insertion order, so duplicate
//! edges keep duplicate groups (this is what lets `UNION ALL` preserve
//! repeated-edge multiplicity).

use std::collections::HashMap;

use crate::batch::{Scalar, TypedBatch};
use crate::error::{EngineError, EngineResult};
use crate::plan::JoinKey;

/// Pre-built probe side (the edge relation).
pub struct EdgeIndex<'a> {
    edges: &'a TypedBatch,
    edge_key_pos: Vec<usize>,
    groups: HashMap<u64, Vec<usize>>,
}

impl<'a> EdgeIndex<'a> {
    /// Index `edges` on the edge-side columns of `keys`.
    /// Rows containing a null in any join key are not indexed (null never joins).
    pub fn build(edges: &'a TypedBatch, keys: &[JoinKey]) -> EngineResult<Self> {
        let edge_key_pos = keys
            .iter()
            .map(|k| {
                edges.column_index(&k.edge).ok_or_else(|| {
                    EngineError::invalid_plan(format!("unknown edge column '{}'", k.edge))
                })
            })
            .collect::<EngineResult<Vec<_>>>()?;

        let mut groups: HashMap<u64, Vec<usize>> = HashMap::new();
        for (edge_idx, row) in edges.rows().iter().enumerate() {
            if let Some(hash) = key_hash(row, &edge_key_pos) {
                groups.entry(hash).or_default().push(edge_idx);
            }
        }
        Ok(Self {
            edges,
            edge_key_pos,
            groups,
        })
    }

    pub fn edge_batch(&self) -> &'a TypedBatch {
        self.edges
    }

    /// Probe with one frontier row. Returns matched edge row indices in
    /// stable insertion order. A null in any join key matches nothing.
    pub fn probe(&self, frontier_row: &[Scalar], recursive_key_pos: &[usize]) -> Vec<usize> {
        let hash = match key_hash(frontier_row, recursive_key_pos) {
            Some(h) => h,
            None => return Vec::new(),
        };
        match self.groups.get(&hash) {
            Some(candidates) => candidates
                .iter()
                .copied()
                .filter(|&edge_idx| {
                    // Full equality, never hash equality alone.
                    let edge_row = &self.edges.rows()[edge_idx];
                    recursive_key_pos
                        .iter()
                        .zip(&self.edge_key_pos)
                        .all(|(&r, &e)| scalar_eq(&frontier_row[r], &edge_row[e]))
                })
                .collect(),
            None => Vec::new(),
        }
    }
}

/// Resolve recursive-side join key positions against the view schema.
pub fn recursive_key_positions(view: &TypedBatch, keys: &[JoinKey]) -> EngineResult<Vec<usize>> {
    keys.iter()
        .map(|k| {
            view.column_index(&k.recursive).ok_or_else(|| {
                EngineError::invalid_plan(format!("unknown recursive column '{}'", k.recursive))
            })
        })
        .collect()
}

/// Hash of the key tuple, or `None` when any component is null (null never joins).
fn key_hash(row: &[Scalar], positions: &[usize]) -> Option<u64> {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::Hasher;

    let mut hasher = DefaultHasher::new();
    for &p in positions {
        match &row[p] {
            Scalar::Null => return None,
            other => other.hash_key(&mut hasher),
        }
    }
    Some(hasher.finish())
}

/// Equality for join keys. Only declared-key scalars reach here, but the
/// match stays total. Nulls are excluded upstream.
fn scalar_eq(a: &Scalar, b: &Scalar) -> bool {
    match (a, b) {
        (Scalar::Int(x), Scalar::Int(y)) => x == y,
        (Scalar::Utf8(x), Scalar::Utf8(y)) => x == y,
        (Scalar::Bool(x), Scalar::Bool(y)) => x == y,
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::plan::{ColumnDecl, ColumnType, Relation};
    use serde_json::json;

    fn batch(rows: Vec<Vec<serde_json::Value>>) -> TypedBatch {
        let rel = Relation {
            columns: vec![
                ColumnDecl {
                    name: "src".into(),
                    data_type: ColumnType::Int64,
                },
                ColumnDecl {
                    name: "dst".into(),
                    data_type: ColumnType::Int64,
                },
            ],
            rows,
        };
        TypedBatch::from_relation(&rel).unwrap()
    }

    fn keys() -> Vec<JoinKey> {
        vec![JoinKey {
            recursive: "node".into(),
            edge: "src".into(),
        }]
    }

    #[test]
    fn duplicate_edges_keep_duplicate_groups_in_insertion_order() {
        let edges = batch(vec![
            vec![json!(1), json!(2)],
            vec![json!(1), json!(2)],
            vec![json!(1), json!(3)],
        ]);
        let index = EdgeIndex::build(&edges, &keys()).unwrap();
        let view = TypedBatch::from_relation(&Relation {
            columns: vec![ColumnDecl {
                name: "node".into(),
                data_type: ColumnType::Int64,
            }],
            rows: vec![vec![json!(1)]],
        })
        .unwrap();
        let pos = recursive_key_positions(&view, &keys()).unwrap();
        let hits = index.probe(&view.rows()[0], &pos);
        assert_eq!(hits, vec![0, 1, 2], "bag multiplicity + stable order");
    }

    #[test]
    fn null_key_never_joins() {
        let edges = batch(vec![vec![json!(1), json!(2)]]);
        let index = EdgeIndex::build(&edges, &keys()).unwrap();
        let view = TypedBatch::from_relation(&Relation {
            columns: vec![ColumnDecl {
                name: "node".into(),
                data_type: ColumnType::Int64,
            }],
            rows: vec![vec![json!(null)]],
        })
        .unwrap();
        let pos = recursive_key_positions(&view, &keys()).unwrap();
        assert!(index.probe(&view.rows()[0], &pos).is_empty());
    }
}
