//! Independent explicit-recursion reference oracle.
//!
//! This module deliberately shares **no code** with the execution engine under
//! test: it operates on plain adjacency lists and plain value vectors, and its
//! control flow is a literal recursive function (`walk`) rather than the
//! engine's working-table/accumulator loop. Tests use it to cross-check
//! semantics, alongside fully hand-written expected outputs.
//!
//! The oracle models a graph whose nodes carry a business row (`Vec<Value>`)
//! and whose edges are `(parent_key, child_row)` pairs: following an edge
//! yields the child's business row directly (the test fixtures project edge
//! columns positionally, so the oracle performs that projection once while
//! building adjacency).

use std::collections::BTreeMap;

use crate::batch::Value;

/// One edge in the reference graph: parent key -> child business row,
/// carrying the child key separately for path/cycle logic.
#[derive(Clone, Debug)]
pub struct RefEdge {
    /// Source node key (single-column keys in the shared fixtures).
    pub from: Value,
    /// Destination node key.
    pub to: Value,
    /// Business row produced when this edge is followed.
    pub child_row: Vec<Value>,
}

/// A graph plus seed rows, described without any engine types.
#[derive(Clone, Debug)]
pub struct RefGraph {
    /// Seed business rows (depth 0).
    pub seeds: Vec<Vec<Value>>,
    /// Edges in declared input order.
    pub edges: Vec<RefEdge>,
}

/// One oracle output row.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RefRow {
    /// Business columns.
    pub business: Vec<Value>,
    /// Flattened key path, seed key first.
    pub path: Vec<Value>,
    /// Cycle flag.
    pub cycle: bool,
    /// Depth of the row.
    pub depth: u32,
}

/// Oracle result.
#[derive(Clone, Debug)]
pub struct RefResult {
    /// Rows in explicit-DFS walk order: seeds in order, first edge first.
    pub rows: Vec<RefRow>,
    /// `true` if any row at the depth boundary still had children: the
    /// enumeration was truncated by `max_depth`.
    pub truncated_by_depth: bool,
}

impl RefGraph {
    /// Build adjacency from raw `(from, to)` key pairs, where the child
    /// business row is simply `[to]` (single-business-column fixtures).
    pub fn from_key_edges(
        seed_keys: impl IntoIterator<Item = i64>,
        edges: impl IntoIterator<Item = (i64, i64)>,
    ) -> Self {
        let seeds = seed_keys
            .into_iter()
            .map(|k| vec![Value::Int64(k)])
            .collect();
        let edges = edges
            .into_iter()
            .map(|(from, to)| RefEdge {
                from: Value::Int64(from),
                to: Value::Int64(to),
                child_row: vec![Value::Int64(to)],
            })
            .collect();
        Self { seeds, edges }
    }

    /// Enumerate **every walk** with explicit recursion — the `UNION ALL`
    /// semantics. A walk ends at a cycle-closing node (flagged, not recursed)
    /// or at the depth boundary. Duplicate edges naturally produce duplicate
    /// walks, which are all retained.
    pub fn enumerate_all(&self, max_depth: u32) -> RefResult {
        let mut out = Vec::new();
        let mut truncated = false;
        for seed in &self.seeds {
            let seed_key = seed_key_of(seed);
            self.walk(
                seed.clone(),
                vec![seed_key],
                0,
                max_depth,
                &mut out,
                &mut truncated,
            );
        }
        RefResult {
            rows: out,
            truncated_by_depth: truncated,
        }
    }

    /// Explicit recursive walk — the mathematical definition written down
    /// directly, with no working table or frontier.
    fn walk(
        &self,
        business: Vec<Value>,
        path: Vec<Value>,
        depth: u32,
        max_depth: u32,
        out: &mut Vec<RefRow>,
        truncated: &mut bool,
    ) {
        let own_key = business.last().cloned().expect("business row has a key");
        let cycle = path
            .iter()
            .take(path.len().saturating_sub(1))
            .any(|k| k == &own_key);
        out.push(RefRow {
            business: business.clone(),
            path: path.clone(),
            cycle,
            depth,
        });
        if cycle {
            return;
        }
        let children: Vec<&RefEdge> = self.edges.iter().filter(|e| e.from == own_key).collect();
        if depth >= max_depth {
            if !children.is_empty() {
                *truncated = true;
            }
            return;
        }
        for edge in children {
            let mut child_path = path.clone();
            child_path.push(edge.to.clone());
            self.walk(
                edge.child_row.clone(),
                child_path,
                depth + 1,
                max_depth,
                out,
                truncated,
            );
        }
    }

    /// Set semantics: same recursive enumeration, but each row identity
    /// `(business, cycle)` is reported on its first visit only, and revisited
    /// identities are not expanded. Returns the distinct identity multiset
    /// (order-independent; tests compare as a set) plus first-arrival rows.
    pub fn enumerate_distinct(&self, max_depth: u32) -> Vec<RefRow> {
        let all = self.enumerate_all(max_depth);
        let mut seen: std::collections::HashSet<(Vec<Value>, bool)> =
            std::collections::HashSet::new();
        let mut out = Vec::new();
        for row in all.rows {
            let id = (row.business.clone(), row.cycle);
            if seen.insert(id) {
                out.push(row);
            }
        }
        out
    }

    /// Count how many times each distinct walk endpoint business row appears
    /// (multiplicity map used to assert `UNION ALL` bag semantics).
    pub fn multiplicity(rows: &[RefRow]) -> BTreeMap<(Vec<Value>, bool), usize> {
        let mut map = BTreeMap::new();
        for r in rows {
            *map.entry((r.business.clone(), r.cycle)).or_insert(0) += 1;
        }
        map
    }
}

fn seed_key_of(row: &[Value]) -> Value {
    row.last()
        .cloned()
        .expect("seed row must carry its key in the single-column fixtures")
}
