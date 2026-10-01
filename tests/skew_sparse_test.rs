//! Highly-skewed and sparse-intersection data: verify exact results against
//! the oracle and prove LFJ avoids the naive intermediate blowup.
mod common;

use common::*;
use leapfrog_triejoin::domain::{Datum, LogicalType};

/// Skew case:
/// ab(0,b for b in 0..1000) + (1,0); ac(0,c for c in 0..1000); pick_b(0).
fn skew_case() -> Case {
    let mut ab: Vec<Vec<Datum>> = (0..1000).map(|b| ints(&[0, b])).collect();
    ab.push(ints(&[1, 0]));
    let ac: Vec<Vec<Datum>> = (0..1000).map(|c| ints(&[0, c])).collect();
    let pick = vec![ints(&[0])];
    let r1 = rel(
        "ab",
        &[("a", LogicalType::Int64), ("b", LogicalType::Int64)],
        ab,
    );
    let r2 = rel(
        "ac",
        &[("a", LogicalType::Int64), ("c", LogicalType::Int64)],
        ac,
    );
    let r3 = rel("pick_b", &[("b", LogicalType::Int64)], pick);
    compile_case(req(vec![r1, r2, r3]))
}

#[test]
fn skew_exact_thousand_results() {
    let case = skew_case();
    let out = engine_rows(&case, 10_000, None);

    // Hand-computed: (a=0,b=0,c=0..999).
    assert_eq!(out.rows.len(), 1000);
    for (i, row) in out.rows.iter().enumerate() {
        let v = as_ints(&row.values);
        // canonical output attrs: join vars a,b,c then privates.
        assert_eq!(v[0], 0, "a=0");
        assert_eq!(v[1], 0, "b=0");
        assert_eq!(v[2], i as i64, "c runs 0..999 in order");
        assert_eq!(row.multiplicity, 1);
    }
}

#[test]
fn skew_avoids_million_row_intermediate() {
    let case = skew_case();
    let out = engine_rows(&case, 10_000, None);
    let oracle = leapfrog_triejoin::join::naive::naive_join(
        &case.inputs,
        &case.plan.select,
        case.request.null_policy,
    )
    .unwrap();

    // Both must agree exactly.
    assert_eq!(out.rows.len(), oracle.rows.len());
    for (g, n) in out.rows.iter().zip(oracle.rows.iter()) {
        assert_eq!(g.values, n.values);
        assert_eq!(g.multiplicity, n.multiplicity);
    }

    // The naive left-deep pipeline materializes a ~1,000,000-row intermediate.
    assert!(
        oracle.stats.max_intermediate >= 1_000_000,
        "naive should build a million-row intermediate, got {}",
        oracle.stats.max_intermediate
    );

    // LFJ never enumerates a product: emitted assignments equal the answer and
    // seek work stays small. There is no intermediate to measure because none
    // is allocated; bounded emitted/seek counts are the observable guarantee.
    assert_eq!(out.counters.emitted_multiplicity, 1000);
    assert!(
        out.counters.seek_key_comparisons < 500,
        "LFJ seeks sparsely, got {}",
        out.counters.seek_key_comparisons
    );
    assert!(
        out.counters.value_probes < 5000,
        "LFJ does not touch every ab/ac pair, got {}",
        out.counters.value_probes
    );
}

/// Sparse case: two ~2000-key domains occupying disjoint ranges, intersecting
/// on only 3 keys (5, 7, 9) planted into the otherwise-distant second range.
fn sparse_case() -> Case {
    let x: Vec<Vec<Datum>> = (0..2000i64).map(|k| ints(&[k, k + 1])).collect();
    let mut y: Vec<Vec<Datum>> = (2001..=4001).map(|k| ints(&[k, k * 10])).collect();
    y.extend_from_slice(&[ints(&[5, 50]), ints(&[7, 70]), ints(&[9, 90])]);
    let r1 = rel(
        "x",
        &[("k", LogicalType::Int64), ("v", LogicalType::Int64)],
        x,
    );
    let r2 = rel(
        "y",
        &[("k", LogicalType::Int64), ("w", LogicalType::Int64)],
        y,
    );
    compile_case(req(vec![r1, r2]))
}

#[test]
fn sparse_intersection_is_three_keys() {
    let case = sparse_case();
    let out = engine_rows(&case, 1000, None);

    let keys: Vec<i64> = out.rows.iter().map(|r| as_ints(&r.values)[0]).collect();
    assert_eq!(keys, vec![5, 7, 9]);
    for row in &out.rows {
        assert_eq!(row.multiplicity, 1);
    }

    // The domains are large and far apart; intersection finishes in a handful
    // of binary leaps (3 aligned keys + 1 terminal overshoot), never a
    // 2000-position linear merge.
    assert!(
        out.counters.seek_calls <= 6,
        "sparse intersection needs ~4 seeks, got {}",
        out.counters.seek_calls
    );
    assert!(
        out.counters.next_calls <= 8,
        "must not walk the disjoint ranges, got {}",
        out.counters.next_calls
    );

    let oracle = oracle_rows(&case);
    assert_eq!(out.rows.len(), oracle.len());
    for (g, n) in out.rows.iter().zip(oracle.iter()) {
        assert_eq!(g.values, n.values);
        assert_eq!(g.multiplicity, n.multiplicity);
    }
}
