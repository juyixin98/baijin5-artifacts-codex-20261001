//! Pagination, cursor resumption, projection aggregation, and multiplicity.
mod common;

use common::*;
use leapfrog_triejoin::domain::{Datum, LogicalType};

/// Three small relations whose join yields a known, easily-paged answer.
fn chain_case() -> Case {
    // ab(a,b): a in 1..=3 -> b in 1..=3 (9)
    let ab: Vec<Vec<Datum>> = (1..=3)
        .flat_map(|a| (1..=3).map(move |b| ints(&[a, b])))
        .collect();
    // bc(b,c): b in 1..=3 -> c in 1..=3 (9)
    let bc: Vec<Vec<Datum>> = (1..=3)
        .flat_map(|b| (1..=3).map(move |c| ints(&[b, c])))
        .collect();
    let r1 = rel(
        "ab",
        &[("a", LogicalType::Int64), ("b", LogicalType::Int64)],
        ab,
    );
    let r2 = rel(
        "bc",
        &[("b", LogicalType::Int64), ("c", LogicalType::Int64)],
        bc,
    );
    compile_case(req(vec![r1, r2]))
}

#[test]
fn full_join_has_27_results_sorted() {
    let case = chain_case();
    let out = engine_rows(&case, 1000, None);
    assert_eq!(out.rows.len(), 27);
    // Lexicographic ascending order across all three variables.
    let mut sorted = out.rows.clone();
    sorted.sort_by(|x, y| x.values.cmp(&y.values));
    assert_eq!(
        out.rows.iter().map(|r| &r.values).collect::<Vec<_>>(),
        sorted.iter().map(|r| &r.values).collect::<Vec<_>>()
    );
}

#[test]
fn pages_stitch_without_gaps_or_duplicates() {
    let case = chain_case();
    let mut seen: Vec<Vec<i64>> = Vec::new();
    let mut cursor: Option<String> = None;
    let mut pages = 0;

    loop {
        let out = engine_rows(&case, 5, cursor.clone());
        assert!(out.rows.len() <= 5);
        for r in &out.rows {
            seen.push(as_ints(&r.values));
        }
        pages += 1;
        match out.next_cursor {
            Some(next) => {
                assert_eq!(out.rows.len(), 5, "non-final pages are full");
                cursor = Some(next);
            }
            None => {
                assert!(out.rows.len() <= 5);
                break;
            }
        }
        assert!(pages <= 6, "27 rows / 5 => 6 pages, got {pages}");
    }

    assert_eq!(seen.len(), 27, "stitched pages reconstruct all rows");
    let unique: std::collections::HashSet<Vec<i64>> = seen.iter().cloned().collect();
    assert_eq!(unique.len(), 27, "no duplicate row across pages");
    // The last page contains 2 rows (27 = 5*5 + 2).
    assert_eq!(pages, 6);
}

#[test]
fn resuming_with_a_garbage_cursor_is_rejected() {
    let case = chain_case();
    let err = leapfrog_triejoin::join::decode_after("!!!not-base64!!!", &case.plan.output_types)
        .unwrap_err();
    assert_eq!(err.code, leapfrog_triejoin::error::ErrorCode::InvalidCursor);
}

#[test]
fn resuming_with_wrong_arity_cursor_is_rejected() {
    let case = chain_case();
    let token = leapfrog_triejoin::join::encode_after(&[leapfrog_triejoin::domain::Datum::Int(1)]);
    let err = leapfrog_triejoin::join::decode_after(&token, &case.plan.output_types).unwrap_err();
    assert_eq!(err.code, leapfrog_triejoin::error::ErrorCode::InvalidCursor);
}

#[test]
fn projection_aggregates_bag_multiplicity() {
    // Join on b then project only a: each a value appears for 3 b * 3 c = 9.
    let case = chain_case();
    let mut request = case.request.clone();
    request.select = Some(vec!["a".to_string()]);
    let projected = compile_case(request);
    let out = engine_rows(&projected, 100, None);

    let got: Vec<(i64, u128)> = out
        .rows
        .iter()
        .map(|r| (as_ints(&r.values)[0], r.multiplicity))
        .collect();
    assert_eq!(got, vec![(1, 9), (2, 9), (3, 9)]);

    // Cross-check the aggregated multiset against the independent oracle.
    let oracle = oracle_rows(&projected);
    assert_eq!(oracle.len(), 3);
    for (g, n) in out.rows.iter().zip(oracle.iter()) {
        assert_eq!(g.values, n.values);
        assert_eq!(g.multiplicity, n.multiplicity);
    }
}

#[test]
fn projection_pagination_never_splits_a_group() {
    let case = chain_case();
    let mut request = case.request.clone();
    request.select = Some(vec!["a".to_string()]);
    let projected = compile_case(request);

    // There are only 3 distinct projected keys; a tiny page still returns
    // whole aggregated groups, never a partial multiplicity.
    let p1 = engine_rows(&projected, 1, None);
    assert_eq!(p1.rows.len(), 1);
    assert_eq!(as_ints(&p1.rows[0].values), vec![1]);
    assert_eq!(p1.rows[0].multiplicity, 9);
    assert!(p1.truncated);

    let p2 = engine_rows(&projected, 1, p1.next_cursor);
    assert_eq!(as_ints(&p2.rows[0].values), vec![2]);
    assert_eq!(p2.rows[0].multiplicity, 9);
}

#[test]
fn single_relation_scan_preserves_order_multiplicity_and_paging() {
    // Arity-1 restricted join: no shared variable; scan the one trie.
    let t = rel(
        "t",
        &[("k", LogicalType::Int64), ("v", LogicalType::Int64)],
        vec![
            ints(&[3, 30]),
            ints(&[1, 10]),
            ints(&[1, 10]), // duplicate -> multiplicity 2
            ints(&[2, 20]),
        ],
    );
    let case = compile_case(req(vec![t]));
    assert!(case.plan.join_vars.is_empty());

    // Sorted distinct tuples with bag multiplicity.
    let out = engine_rows(&case, 100, None);
    let got: Vec<(Vec<i64>, u128)> = out
        .rows
        .iter()
        .map(|r| (as_ints(&r.values), r.multiplicity))
        .collect();
    assert_eq!(
        got,
        vec![(vec![1, 10], 2), (vec![2, 20], 1), (vec![3, 30], 1),]
    );

    // Pagination resumes correctly on the single-relation path.
    let p1 = engine_rows(&case, 1, None);
    assert!(p1.truncated);
    let p2 = engine_rows(&case, 10, p1.next_cursor);
    let rest: Vec<Vec<i64>> = p2.rows.iter().map(|r| as_ints(&r.values)).collect();
    assert_eq!(rest, vec![vec![2, 20], vec![3, 30]]);
    assert!(!p2.truncated);
}

#[test]
fn duplicate_rows_produce_multiplied_multiplicity() {
    let l = rel(
        "l",
        &[("x", LogicalType::Int64), ("y", LogicalType::Int64)],
        vec![
            ints(&[1, 10]),
            ints(&[1, 10]),
            ints(&[1, 10]),
            ints(&[2, 20]),
        ],
    );
    let r = rel(
        "r",
        &[("x", LogicalType::Int64), ("z", LogicalType::Int64)],
        vec![ints(&[1, 100]), ints(&[1, 100])],
    );
    let case = compile_case(req(vec![l, r]));
    let out = engine_rows(&case, 100, None);
    // (x=1,y=10,z=100) multiplicity 3*2 = 6.
    assert_eq!(out.rows.len(), 1);
    assert_eq!(as_ints(&out.rows[0].values), vec![1, 10, 100]);
    assert_eq!(out.rows[0].multiplicity, 6);
}
