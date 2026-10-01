//! Highly-skewed and sparse-intersection verification.
//!
//! The hub relations R and S agree on 200 x 200 = 40,000 combinations through
//! the shared value b=1, but the third relation T intersects only five of
//! them. A binary-plan / nested-loops backend must materialise tens of
//! thousands of intermediate prefix tuples before T filters them away;
//! Leapfrog intersects all three variable domains directly and emits the five
//! answers with a small access budget.

mod support;

use leapfrog_triejoin::lftj::JoinEngine;
use leapfrog_triejoin::naive::NaiveOracle;
use leapfrog_triejoin::plan::{NullPolicy, Plan};
use leapfrog_triejoin::value::{Cell, Scalar};

use support::{skew_expected_rows, skew_relations};

fn ints(row: &[Cell]) -> Vec<i64> {
    row.iter()
        .map(|c| match c {
            Cell::Value(Scalar::Int(i)) => *i,
            other => panic!("expected int, got {other:?}"),
        })
        .collect()
}

#[test]
fn skew_join_result_is_exactly_the_five_hand_derived_rows() {
    let plan = Plan::natural_join(skew_relations(), NullPolicy::Reject, 6).unwrap();
    let out = JoinEngine::build(plan).run(None, None, None);

    assert_eq!(out.stats.emitted_rows, 5);
    let mut got: Vec<Vec<i64>> = out.rows.iter().map(|r| ints(r)).collect();
    got.sort();
    let mut expected = skew_expected_rows();
    expected.sort();
    assert_eq!(got, expected);
}

#[test]
fn skew_join_matches_naive_oracle_as_second_opinion() {
    let plan = Plan::natural_join(skew_relations(), NullPolicy::Reject, 6).unwrap();
    let engine = JoinEngine::build(plan.clone()).run(None, None, None);
    let oracle = NaiveOracle::new(&plan).enumerate();

    let mut a: Vec<Vec<i64>> = engine.rows.iter().map(|r| ints(r)).collect();
    let mut b: Vec<Vec<i64>> = oracle.rows.iter().map(|r| ints(r)).collect();
    a.sort();
    b.sort();
    assert_eq!(a, b);
    assert_eq!(engine.stats.emitted_rows, oracle.stats.emitted_rows);
}

#[test]
fn skew_join_avoids_the_cartesian_prefix_explosion() {
    let plan = Plan::natural_join(skew_relations(), NullPolicy::Reject, 6).unwrap();
    let engine = JoinEngine::build(plan.clone()).run(None, None, None);
    let oracle = NaiveOracle::new(&plan).enumerate();

    // Naive: 300 R rows + (200*200 hub + 100 selective) R⋈S prefixes + 5
    // final tuples = 40,405 materialised intermediate tuples.
    assert_eq!(oracle.stats.intermediate_tuples_materialized, 40_405);
    // The hub alone manufactures 40,000 prefixes that T then rejects.
    assert!(oracle.stats.row_probes >= 40_000);

    // Leapfrog: zero intermediates, and far fewer accesses than the naive
    // backend's 40k-prefix detour, for the identical five-row answer.
    assert_eq!(engine.stats.intermediate_tuples_materialized, 0);
    let leapfrog_accesses = engine.stats.seeks
        + engine.stats.seek_comparisons
        + engine.stats.nexts
        + engine.stats.child_opens;
    assert!(
        leapfrog_accesses < 3_000,
        "skew join should need well under 3k trie accesses, got {leapfrog_accesses}"
    );
    assert!(
        leapfrog_accesses * 20 < oracle.stats.intermediate_tuples_materialized,
        "expected at least a 20x gap: {leapfrog_accesses} vs {}",
        oracle.stats.intermediate_tuples_materialized
    );
}
