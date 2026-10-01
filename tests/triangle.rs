//! Triangle query verification:
//! results are asserted against hand-derived counts, the independent naive
//! oracle, and access counters that show no Cartesian blow-up.

mod support;

use leapfrog_triejoin::lftj::{JoinEngine, StopReason};
use leapfrog_triejoin::naive::NaiveOracle;
use leapfrog_triejoin::plan::{NullPolicy, Plan};
use leapfrog_triejoin::value::{Cell, Scalar};

use support::{complete_graph_edges, int_relation, triangle_count, triangle_relations};

fn ints(row: &[Cell]) -> Vec<i64> {
    row.iter()
        .map(|c| match c {
            Cell::Value(Scalar::Int(i)) => *i,
            other => panic!("expected int, got {other:?}"),
        })
        .collect()
}

#[test]
fn triangle_k5_has_exactly_ten_triangles() {
    // C(5,3) = 10, hand-derived; rows enumerated explicitly.
    let plan = Plan::natural_join(triangle_relations(5), NullPolicy::Reject, 6).unwrap();
    let out = JoinEngine::build(plan).run(None, None, None);

    assert_eq!(out.stats.emitted_rows, 10);
    assert_eq!(out.stats.emitted_rows, triangle_count(5));
    assert_eq!(out.stats.stop_reason, StopReason::Complete);
    assert_eq!(out.stats.intermediate_tuples_materialized, 0);

    let mut rows: Vec<Vec<i64>> = out.rows.iter().map(|r| ints(r)).collect();
    rows.sort();
    let mut expected = Vec::new();
    for i in 0..5 {
        for j in (i + 1)..5 {
            for k in (j + 1)..5 {
                expected.push(vec![i, j, k]);
            }
        }
    }
    assert_eq!(rows, expected);
}

#[test]
fn triangle_matches_naive_oracle_for_several_graph_sizes() {
    for n in 1..=8 {
        let plan = Plan::natural_join(triangle_relations(n), NullPolicy::Reject, 6).unwrap();
        let engine = JoinEngine::build(plan.clone()).run(None, None, None);
        let oracle = NaiveOracle::new(&plan).enumerate();

        assert_eq!(
            engine.stats.emitted_rows, oracle.stats.emitted_rows,
            "row count differs for K{n}"
        );
        assert_eq!(engine.stats.emitted_rows, triangle_count(n as u64), "K{n}");

        let mut a: Vec<Vec<i64>> = engine.rows.iter().map(|r| ints(r)).collect();
        let mut b: Vec<Vec<i64>> = oracle.rows.iter().map(|r| ints(r)).collect();
        a.sort();
        b.sort();
        assert_eq!(a, b, "multiset differs for K{n}");
    }
}

#[test]
fn triangle_k200_engine_materialises_no_intermediate_at_all() {
    // K200: each edge relation has C(200,2) = 19_900 rows. A plan that built
    // the R(a,b) ⋈ S(b,c) prefix before filtering with T materialises every
    // length-2 path. With oriented edges a<b<c on a complete graph the path
    // count equals C(200,3) = 1,313,400; the oracle counts root rows, paths
    // and leaf tuples: 19,900 + 1,313,400 + 1,313,400 = 2,646,700.
    let plan = Plan::natural_join(triangle_relations(200), NullPolicy::Reject, 6).unwrap();
    let engine = JoinEngine::build(plan.clone()).run(None, None, None);
    let oracle = NaiveOracle::new(&plan).enumerate();

    assert_eq!(engine.stats.emitted_rows, 1_313_400);
    assert_eq!(engine.stats.emitted_rows, triangle_count(200));
    assert_eq!(oracle.stats.emitted_rows, 1_313_400);
    assert_eq!(
        oracle.stats.intermediate_tuples_materialized, 2_646_700,
        "hand-derived: 19_900 roots + 1,313,400 paths + 1,313,400 leaves"
    );

    // The decisive invariant: the Leapfrog backend builds ZERO intermediate
    // tuples, regardless of how large the two-relation prefix would be.
    assert_eq!(engine.stats.intermediate_tuples_materialized, 0);
}

#[test]
fn triangle_with_duplicate_edges_multiplies_results() {
    // Duplicate every edge once: K4 (4 triangles) becomes 4 * 2^3 = 32 rows.
    let mut edges = complete_graph_edges(4);
    let duplicated = edges.clone();
    edges.extend(duplicated);

    let r = int_relation("R", &["a", "b"], edges.clone());
    let s = int_relation("S", &["b", "c"], edges.clone());
    let t = int_relation("T", &["a", "c"], edges);

    let plan = Plan::natural_join(vec![r, s, t], NullPolicy::Reject, 6).unwrap();
    let engine = JoinEngine::build(plan.clone()).run(None, None, None);
    let oracle = NaiveOracle::new(&plan).enumerate();

    assert_eq!(engine.stats.emitted_rows, 32);
    assert_eq!(engine.stats.emitted_rows, oracle.stats.emitted_rows);
    let mut a: Vec<Vec<i64>> = engine.rows.iter().map(|r| ints(r)).collect();
    let mut b: Vec<Vec<i64>> = oracle.rows.iter().map(|r| ints(r)).collect();
    a.sort();
    b.sort();
    assert_eq!(a, b);
}
