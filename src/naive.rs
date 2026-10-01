//! Independent nested-loops reference oracle.
//!
//! This is deliberately **not** a reuse of the Leapfrog engine: it binds the
//! relations one at a time straight from row multisets, using a hash index on
//! each relation's shared columns for probing, and **materialises every
//! surviving prefix tuple** exactly the way the requirement forbids for the
//! production backend. It is an independent binary-join full enumeration:
//!
//! 1. relations are bound in a greedy order that keeps the bound hypergraph
//!    connected (always possible because the planner proved connectivity);
//! 2. relation 0 contributes all its rows; each later relation is probed via
//!    an index keyed on the columns it shares with already-bound attributes;
//! 3. every surviving candidate extends the prefix — that extension is the
//!    intermediate product whose size is reported.
//!
//! Tests use it to cross-check the Leapfrog result and to contrast its
//! materialised-prefix count with the engine's always-zero counter.

use std::collections::HashMap;

use crate::plan::Plan;
use crate::value::Cell;

/// Counters for the naive enumeration.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct NaiveStats {
    /// Index probes performed (including unsuccessful ones).
    pub row_probes: u64,
    /// Prefix tuples materialised while joining (the intermediate product),
    /// counting root rows, every extended prefix and each final row.
    pub intermediate_tuples_materialized: u64,
    /// Complete output rows, with multiplicity.
    pub emitted_rows: u64,
}

/// Result of a naive run.
#[derive(Debug, Clone)]
pub struct NaiveResult {
    pub rows: Vec<Vec<Cell>>,
    pub stats: NaiveStats,
}

/// One relation prepared for indexed binding.
struct Prepared<'a> {
    rows: Vec<&'a Vec<Cell>>,
    /// (source column index -> global attribute index) for this relation.
    col_to_global: Vec<(usize, usize)>,
    /// Index from values of the bound shared columns to matching rows.
    index: Option<HashMap<Vec<Cell>, Vec<usize>>>,
    /// Source column indices that form the probe key, in stable order.
    key_cols: Vec<usize>,
}

/// Nested-loops full enumeration over a validated [`Plan`].
pub struct NaiveOracle<'a> {
    plan: &'a Plan,
}

impl<'a> NaiveOracle<'a> {
    pub fn new(plan: &'a Plan) -> Self {
        NaiveOracle { plan }
    }

    /// Enumerate every binding, projected into the plan's user-facing output
    /// order.
    pub fn enumerate(&self) -> NaiveResult {
        let order = self.binding_order();

        // Prepare each relation in bind order with NULL policy applied.
        let mut prepared: Vec<Prepared<'a>> = order
            .iter()
            .map(|&relation_index| {
                let rel = &self.plan.relations[relation_index];
                let col_to_global: Vec<(usize, usize)> = rel
                    .columns
                    .iter()
                    .enumerate()
                    .map(|(col_idx, col)| (col_idx, self.plan.global_index[&col.name]))
                    .collect();
                Prepared {
                    rows: self.plan.rows_for_relation(relation_index),
                    col_to_global,
                    index: None,
                    key_cols: Vec::new(),
                }
            })
            .collect();

        // Build each probe index from the attributes bound by earlier
        // relations in the chosen order.
        let mut bound: std::collections::HashSet<usize> = std::collections::HashSet::new();
        for (slot, entry) in prepared.iter_mut().enumerate() {
            if slot == 0 {
                for &(_, global) in &entry.col_to_global {
                    bound.insert(global);
                }
                continue;
            }
            let key_cols: Vec<usize> = entry
                .col_to_global
                .iter()
                .filter(|(_, global)| bound.contains(global))
                .map(|(col_idx, _)| *col_idx)
                .collect();

            let mut index: HashMap<Vec<Cell>, Vec<usize>> = HashMap::new();
            for (row_idx, row) in entry.rows.iter().enumerate() {
                let key: Vec<Cell> = key_cols.iter().map(|c| row[*c].clone()).collect();
                index.entry(key).or_default().push(row_idx);
            }
            entry.key_cols = key_cols;
            entry.index = Some(index);

            for &(_, global) in &entry.col_to_global {
                bound.insert(global);
            }
        }

        let mut ctx = NaiveCtx {
            plan: self.plan,
            prepared: &prepared,
            prefix: Vec::new(),
            rows: Vec::new(),
            stats: NaiveStats::default(),
        };
        ctx.bind(0);
        NaiveResult {
            rows: ctx.rows,
            stats: ctx.stats,
        }
    }

    /// Bind order for the deliberately naive binary plan.
    ///
    /// Relations are bound in **request order**, so the tree is exactly the
    /// shape the requirement warns about — e.g. `((R ⋈ S) ⋈ T)` materialises
    /// the large `R ⋈ S` prefix before T filters it. Request order is kept
    /// unless the next relation shares no attribute with the already-bound
    /// prefix; in that case the first prefix-connecting later relation moves
    /// forward (connectivity, proven by the planner, guarantees one exists).
    fn binding_order(&self) -> Vec<usize> {
        let n = self.plan.relations.len();
        let mut chosen = vec![false; n];
        let mut order = Vec::with_capacity(n);
        let mut bound: std::collections::HashSet<usize> = std::collections::HashSet::new();

        // First relation: request order (index 0).
        chosen[0] = true;
        order.push(0);
        for col in &self.plan.relations[0].columns {
            bound.insert(self.plan.global_index[&col.name]);
        }

        while order.len() < n {
            let connects = |i: usize| {
                self.plan.relations[i]
                    .columns
                    .iter()
                    .any(|c| bound.contains(&self.plan.global_index[&c.name]))
            };
            let next = (0..n)
                .find(|&i| !chosen[i] && connects(i))
                .expect("connectivity guarantees a prefix-connecting relation");
            chosen[next] = true;
            order.push(next);
            for col in &self.plan.relations[next].columns {
                bound.insert(self.plan.global_index[&col.name]);
            }
        }
        order
    }
}

struct NaiveCtx<'a, 'b> {
    plan: &'b Plan,
    prepared: &'b [Prepared<'a>],
    /// global attribute index -> cell, for the current prefix.
    prefix: Vec<(usize, Cell)>,
    rows: Vec<Vec<Cell>>,
    stats: NaiveStats,
}

impl<'a, 'b> NaiveCtx<'a, 'b> {
    fn bind(&mut self, slot: usize) {
        if slot == self.prepared.len() {
            // Project the complete prefix into user-facing output order.
            let by_attr: HashMap<usize, Cell> = self.prefix.iter().cloned().collect();
            let row =
                self.plan
                    .output_columns
                    .iter()
                    .map(|(name, _)| {
                        let g = self.plan.global_index[name];
                        by_attr.get(&g).cloned().unwrap_or_else(|| {
                            panic!("output attribute {g} unbound in full prefix")
                        })
                    })
                    .collect();
            self.rows.push(row);
            self.stats.emitted_rows += 1;
            self.stats.intermediate_tuples_materialized += 1;
            return;
        }

        let prepared = &self.prepared[slot];
        let candidates: Vec<usize> = if slot == 0 {
            (0..prepared.rows.len()).collect()
        } else {
            // Build the probe key from the current prefix.
            let key: Vec<Cell> = prepared
                .key_cols
                .iter()
                .map(|col_idx| {
                    let global = *prepared
                        .col_to_global
                        .iter()
                        .find(|(c, _)| c == col_idx)
                        .map(|(_, g)| g)
                        .expect("key column is mapped");
                    self.prefix
                        .iter()
                        .find(|(g, _)| *g == global)
                        .map(|(_, cell)| cell.clone())
                        .expect("key attribute is bound")
                })
                .collect();
            self.stats.row_probes += 1;
            prepared
                .index
                .as_ref()
                .expect("index built for non-root relation")
                .get(&key)
                .cloned()
                .unwrap_or_default()
        };

        for row_idx in candidates {
            let candidate: &Vec<Cell> = prepared.rows[row_idx];

            // Materialise the extended prefix (the intermediate product).
            let mark = self.prefix.len();
            for &(col_idx, global) in &prepared.col_to_global {
                if !self.prefix.iter().any(|(g, _)| *g == global) {
                    self.prefix.push((global, candidate[col_idx].clone()));
                }
            }
            if slot != self.prepared.len() - 1 {
                self.stats.intermediate_tuples_materialized += 1;
            }
            self.bind(slot + 1);
            while self.prefix.len() > mark {
                self.prefix.pop();
            }
        }
    }
}

/// Convenience: full plan -> multiset result via the oracle.
pub fn enumerate_plan(plan: &Plan) -> NaiveResult {
    NaiveOracle::new(plan).enumerate()
}

/// Multiset equality of two row batches (order-insensitive).
pub fn multisets_equal(a: &[Vec<Cell>], b: &[Vec<Cell>]) -> bool {
    let mut a: Vec<&Vec<Cell>> = a.iter().collect();
    let mut b: Vec<&Vec<Cell>> = b.iter().collect();
    a.sort();
    b.sort();
    a == b
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lftj::JoinEngine;
    use crate::plan::NullPolicy;
    use crate::schema::{Column, ColumnType, Relation};
    use crate::value::Scalar;

    fn rel(name: &str, cols: &[&str], rows: Vec<Vec<i64>>) -> Relation {
        let mut r = Relation::new(
            name,
            cols.iter()
                .map(|c| Column::new(*c, ColumnType::Int))
                .collect(),
        );
        r.set_rows(
            rows.into_iter()
                .map(|row| row.into_iter().map(Cell::from).collect())
                .collect(),
        )
        .unwrap();
        r
    }

    #[test]
    fn oracle_emits_all_duplicates() {
        // 3 R rows, each joins 3 S rows = 9 outputs.
        let r = rel("R", &["a", "b"], vec![vec![1, 1], vec![1, 1], vec![2, 1]]);
        let s = rel("S", &["b", "c"], vec![vec![1, 9], vec![1, 9], vec![1, 9]]);
        let plan = Plan::natural_join(vec![r, s], NullPolicy::Reject, 6).unwrap();
        let got = enumerate_plan(&plan);
        assert_eq!(got.stats.emitted_rows, 9);
        // Request-order binary plan: 3 root R rows (+3), then 9 final rows
        // (+9) = 12 materialised tuples.
        assert_eq!(got.stats.intermediate_tuples_materialized, 12);
    }

    #[test]
    fn oracle_and_engine_agree_on_triangle() {
        let edge = |name: &str, pair: (&str, &str), n: i64| {
            let rows: Vec<Vec<i64>> = (0..n)
                .flat_map(|i| (i + 1..n).map(move |j| vec![i, j]))
                .collect();
            rel(name, &[pair.0, pair.1], rows)
        };
        let plan = Plan::natural_join(
            vec![
                edge("R", ("a", "b"), 5),
                edge("S", ("b", "c"), 5),
                edge("T", ("a", "c"), 5),
            ],
            NullPolicy::Reject,
            6,
        )
        .unwrap();

        let engine = JoinEngine::build(plan.clone()).run(None, None, None);
        let oracle = enumerate_plan(&plan);
        assert_eq!(engine.stats.emitted_rows, oracle.stats.emitted_rows);
        assert!(multisets_equal(&engine.rows, &oracle.rows));
        // K5 triangles = C(5,3) = 10.
        assert_eq!(engine.stats.emitted_rows, 10);
        let _ = Scalar::Int(0);
    }
}
