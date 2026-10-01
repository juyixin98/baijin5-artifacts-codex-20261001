//! Restricted *multi-table* joins beyond three relations:
//! a four-table chain, a five-table star, and the six-relation envelope
//! boundary. Answers are hand-derived; the independent naive oracle is used
//! as a second opinion.

mod support;

use leapfrog_triejoin::lftj::JoinEngine;
use leapfrog_triejoin::naive::NaiveOracle;
use leapfrog_triejoin::plan::{NullPolicy, Plan};
use leapfrog_triejoin::value::{Cell, Scalar};
use support::int_relation;

fn ints(row: &[Cell]) -> Vec<i64> {
    row.iter()
        .map(|c| match c {
            Cell::Value(Scalar::Int(i)) => *i,
            other => panic!("expected int, got {other:?}"),
        })
        .collect()
}

#[test]
fn four_table_chain_matches_hand_derived_three_rows() {
    // R(a,b) S(b,c) T(c,d) U(d,e)
    let r = int_relation("R", &["a", "b"], vec![vec![0, 1], vec![1, 2]]);
    let s = int_relation("S", &["b", "c"], vec![vec![1, 10], vec![2, 20]]);
    let t = int_relation("T", &["c", "d"], vec![vec![10, 100], vec![20, 200]]);
    let u = int_relation(
        "U",
        &["d", "e"],
        vec![vec![100, 1000], vec![100, 1001], vec![200, 2000]],
    );

    let plan = Plan::natural_join(vec![r, s, t, u], NullPolicy::Reject, 6).unwrap();
    let engine = JoinEngine::build(plan.clone()).run(None, None, None);
    let oracle = NaiveOracle::new(&plan).enumerate();

    let mut got: Vec<Vec<i64>> = engine.rows.iter().map(|r| ints(r)).collect();
    got.sort();
    // Two paths; the d=100 branch fans out to two e values.
    assert_eq!(
        got,
        vec![
            vec![0, 1, 10, 100, 1000],
            vec![0, 1, 10, 100, 1001],
            vec![1, 2, 20, 200, 2000],
        ]
    );
    assert_eq!(engine.stats.emitted_rows, 3);
    assert_eq!(engine.stats.intermediate_tuples_materialized, 0);
    assert!(leapfrog_triejoin::naive::multisets_equal(
        &engine.rows,
        &oracle.rows
    ));
}

#[test]
fn five_table_star_intersects_all_leaves_on_one_key() {
    // Center C(k) with four leaves Li(k, xi). k=2 is absent from leaf 3, so
    // only k=1 survives the five-way intersection.
    let c = int_relation("C", &["k"], vec![vec![1], vec![2]]);
    let l1 = int_relation("L1", &["k", "x1"], vec![vec![1, 11], vec![2, 21]]);
    let l2 = int_relation("L2", &["k", "x2"], vec![vec![1, 12], vec![2, 22]]);
    let l3 = int_relation("L3", &["k", "x3"], vec![vec![1, 13]]);
    let l4 = int_relation("L4", &["k", "x4"], vec![vec![1, 14]]);

    let plan = Plan::natural_join(vec![c, l1, l2, l3, l4], NullPolicy::Reject, 6).unwrap();
    let engine = JoinEngine::build(plan.clone()).run(None, None, None);
    let oracle = NaiveOracle::new(&plan).enumerate();

    assert_eq!(engine.stats.emitted_rows, 1);
    assert_eq!(
        ints(&engine.rows[0]),
        vec![1, 11, 12, 13, 14],
        "only the key present in all five relations survives"
    );
    assert!(leapfrog_triejoin::naive::multisets_equal(
        &engine.rows,
        &oracle.rows
    ));
}

#[test]
fn six_relation_envelope_accepted_and_seven_rejected() {
    // Chain of six binary relations sharing successive keys k1..k5.
    let make = |name: &str, left: &str, right: &str| {
        int_relation(name, &[left, right], vec![vec![1, 1], vec![2, 2]])
    };
    let six: Vec<_> = vec![
        make("R0", "k1", "k2"),
        make("R1", "k2", "k3"),
        make("R2", "k3", "k4"),
        make("R3", "k4", "k5"),
        make("R4", "k5", "k6"),
        int_relation("R5", &["k6", "z"], vec![vec![1, 9], vec![2, 8]]),
    ];
    let plan = Plan::natural_join(six, NullPolicy::Reject, 6).unwrap();
    let engine = JoinEngine::build(plan).run(None, None, None);
    // Two consistent assignments (all 1s with z=9; all 2s with z=8).
    assert_eq!(engine.stats.emitted_rows, 2);

    // A seventh relation exceeds the restricted envelope.
    let seven: Vec<_> = vec![
        make("R0", "k1", "k2"),
        make("R1", "k2", "k3"),
        make("R2", "k3", "k4"),
        make("R3", "k4", "k5"),
        make("R4", "k5", "k6"),
        int_relation("R5", &["k6", "z"], vec![vec![1, 9]]),
        int_relation("R6", &["z", "w"], vec![vec![9, 0]]),
    ];
    let err = Plan::natural_join(seven, NullPolicy::Reject, 6).unwrap_err();
    assert_eq!(err.code, leapfrog_triejoin::ErrorCode::UnsupportedShape);
}

#[test]
fn disconnected_fourth_relation_is_rejected_not_cross_joined() {
    // Three relations form a connected join; a fourth shares nothing.
    let r = int_relation("R", &["a", "b"], vec![vec![1, 1]]);
    let s = int_relation("S", &["b", "c"], vec![vec![1, 2]]);
    let t = int_relation("T", &["c", "d"], vec![vec![2, 3]]);
    let rogue = int_relation("X", &["zz"], vec![vec![9]]);
    let err = Plan::natural_join(vec![r, s, t, rogue], NullPolicy::Reject, 6).unwrap_err();
    assert_eq!(err.code, leapfrog_triejoin::ErrorCode::UnsupportedShape);
}
