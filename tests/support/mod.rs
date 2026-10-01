//! Shared builders for integration tests.
//!
//! Expected answers here are **hand-derived constants**, never produced by
//! the engine under test. The independent naive oracle lives in the crate and
//! is used as a second opinion, but concrete counts/rows are hard-coded so a
//! bug shared by both algorithms could not make a test pass.

// The support module is compiled independently into every integration-test
// binary; each one only uses a subset of these builders.
#![allow(dead_code)]

use leapfrog_triejoin::schema::{Column, ColumnType, Relation};
use leapfrog_triejoin::validate::{ColumnSpec, RelationSpec};
use leapfrog_triejoin::value::Cell;

/// Build an all-integer relation from literal rows.
pub fn int_relation(name: &str, columns: &[&str], rows: Vec<Vec<i64>>) -> Relation {
    let mut relation = Relation::new(
        name,
        columns
            .iter()
            .map(|c| Column::new(*c, ColumnType::Int))
            .collect(),
    );
    relation
        .set_rows(
            rows.into_iter()
                .map(|r| r.into_iter().map(Cell::from).collect())
                .collect(),
        )
        .expect("fixture relation valid");
    relation
}

/// Wire-format counterpart of [`int_relation`].
pub fn int_spec(name: &str, columns: &[&str], rows: Vec<Vec<i64>>) -> RelationSpec {
    RelationSpec {
        name: name.to_string(),
        columns: columns
            .iter()
            .map(|c| ColumnSpec {
                name: (*c).to_string(),
                typ: "int".to_string(),
            })
            .collect(),
        rows: rows
            .into_iter()
            .map(|r| r.into_iter().map(serde_json::Value::from).collect())
            .collect(),
    }
}

/// Oriented edges i < j of the complete graph K_n.
pub fn complete_graph_edges(n: i64) -> Vec<Vec<i64>> {
    let mut edges = Vec::new();
    for i in 0..n {
        for j in (i + 1)..n {
            edges.push(vec![i, j]);
        }
    }
    edges
}

/// The three oriented edge relations of a triangle query over K_n:
/// R(a,b), S(b,c), T(a,c).
pub fn triangle_relations(n: i64) -> Vec<Relation> {
    let edges = complete_graph_edges(n);
    vec![
        int_relation("R", &["a", "b"], edges.clone()),
        int_relation("S", &["b", "c"], edges.clone()),
        int_relation("T", &["a", "c"], edges),
    ]
}

/// C(n,3): hand-stated closed form used as the independent expected size.
pub fn triangle_count(n: u64) -> u64 {
    if n < 3 {
        return 0;
    }
    n * (n - 1) * (n - 2) / 6
}

/// Hub-and-sparse fixture:
/// - R(a,b): b=1 repeated for 200 distinct `a` plus 100 selective rows;
/// - S(b,c): b=1 repeated for 200 distinct `c` plus 100 selective rows;
/// - T(a,c): five intersecting pairs only.
pub fn skew_relations() -> Vec<Relation> {
    let mut r_rows: Vec<Vec<i64>> = (1..=200).map(|a| vec![a, 1]).collect();
    for b in 2..=101 {
        r_rows.push(vec![b + 1000, b]);
    }

    let mut s_rows: Vec<Vec<i64>> = (1..=200).map(|c| vec![1, c]).collect();
    for b in 2..=101 {
        s_rows.push(vec![b, b + 9000]);
    }

    let t_rows = vec![
        vec![7, 7],
        vec![42, 42],
        vec![100, 100],
        vec![200, 200],
        vec![1002, 9002],
    ];

    vec![
        int_relation("R", &["a", "b"], r_rows),
        int_relation("S", &["b", "c"], s_rows),
        int_relation("T", &["a", "c"], t_rows),
    ]
}

/// Hand-derived expected join rows for [`skew_relations`] (multiset).
pub fn skew_expected_rows() -> Vec<Vec<i64>> {
    // Hub path b=1: T intersects at (7,7),(42,42),(100,100),(200,200)
    // -> rows (a,b,c) with b=1.
    let mut rows = vec![
        vec![7, 1, 7],
        vec![42, 1, 42],
        vec![100, 1, 100],
        vec![200, 1, 200],
    ];
    // Selective path b=2: R has (1002,2), S has (2,9002), T has (1002,9002).
    rows.push(vec![1002, 2, 9002]);
    rows
}

/// Deterministic 32-bit LCG so randomized cases are reproducible across runs.
pub struct Lcg {
    state: u64,
}

impl Lcg {
    pub fn new(seed: u64) -> Self {
        Lcg { state: seed }
    }

    pub fn next_u64(&mut self) -> u64 {
        // Numerical Recipes constants.
        self.state = self
            .state
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        self.state
    }

    pub fn below(&mut self, bound: u64) -> u64 {
        self.next_u64() % bound
    }
}
