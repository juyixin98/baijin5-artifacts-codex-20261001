//! Triangle query over K4: exact results against hand-computed constants and
//! against the independent naive oracle, plus access-count verification.
mod common;

use common::*;
use leapfrog_triejoin::domain::{Datum, LogicalType};

fn triangle_case() -> Case {
    let edges = complete_edges(4);
    let e = rel(
        "e",
        &[("a", LogicalType::Int64), ("b", LogicalType::Int64)],
        edges.clone(),
    );
    let f = rel(
        "f",
        &[("b", LogicalType::Int64), ("c", LogicalType::Int64)],
        edges.clone(),
    );
    let g = rel(
        "g",
        &[("c", LogicalType::Int64), ("a", LogicalType::Int64)],
        edges,
    );
    compile_case(req(vec![e, f, g]))
}

#[test]
fn triangle_k4_has_24_orientations() {
    let case = triangle_case();
    let out = engine_rows(&case, 1000, None);

    // Hand-computed: 4 unordered triangles * 6 cyclic orientations = 24.
    assert_eq!(out.rows.len(), 24, "expected exactly 24 triangle tuples");
    assert!(!out.truncated, "24 rows fit the page");
    assert!(out.next_cursor.is_none(), "exhausted result has no cursor");

    // Every output is three distinct vertices and has multiplicity 1.
    for row in &out.rows {
        let v = as_ints(&row.values);
        assert_eq!(v.len(), 3);
        assert_ne!(v[0], v[1]);
        assert_ne!(v[1], v[2]);
        assert_ne!(v[0], v[2]);
        assert_eq!(row.multiplicity, 1);
    }

    // Spot-check one specific orientation is present exactly once.
    let target = vec![Datum::Int(0), Datum::Int(1), Datum::Int(2)];
    let hits = out.rows.iter().filter(|r| r.values == target).count();
    assert_eq!(hits, 1, "orientation (0,1,2) appears once");
}

#[test]
fn triangle_matches_independent_oracle() {
    let case = triangle_case();
    let out = engine_rows(&case, 1000, None);
    let oracle = oracle_rows(&case);

    assert_eq!(out.rows.len(), oracle.len());
    for (got, want) in out.rows.iter().zip(oracle.iter()) {
        assert_eq!(got.values, want.values);
        assert_eq!(got.multiplicity, want.multiplicity);
    }
}

#[test]
fn triangle_leaps_without_pairwise_scans() {
    let case = triangle_case();
    let out = engine_rows(&case, 1000, None);

    // Each relation has 12 distinct edges and 3 root keys at every level.
    // Intersection of 3 keys via binary search never linear-scans a domain:
    // bound seeks well under a brute-force |E|*|E|*|E| = 1728 comparisons.
    assert!(
        out.counters.seek_key_comparisons < 400,
        "seek comparisons should be logarithmic-ish, got {}",
        out.counters.seek_key_comparisons
    );
    // Every emitted tuple is a genuine join result (no phantom multiplicity).
    assert_eq!(out.counters.emitted_multiplicity, 24);
}
