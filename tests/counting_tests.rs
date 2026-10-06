//! Counting core tests. Expected values are hand-computed here, not
//! produced by the implementation under test.

use dmc::config::Config;
use dmc::count::count_models;
use dmc::proof::ValidationProof;
use dmc::syntax::{Circuit, Node};
use dmc::validate::validate;
use num_bigint::BigUint;
use std::collections::BTreeSet;

fn declared(vars: &[u32]) -> BTreeSet<u32> {
    vars.iter().copied().collect()
}

/// Count without running the validator (counting-only tests).
fn count_unchecked(c: &Circuit, vars: &BTreeSet<u32>) -> BigUint {
    count_models(c, vars, "t-count", ValidationProof::default()).total
}

/// Full pipeline: validate, then count.
fn count_validated(c: &Circuit, vars: &BTreeSet<u32>) -> BigUint {
    let proof = validate(c, &Config::default()).expect("circuit should validate");
    count_models(c, vars, "t-count", proof).total
}

#[test]
fn single_literal_over_own_scope() {
    // x1 : exactly one assignment over {1} satisfies it.
    let c = Circuit {
        root: 0,
        nodes: vec![Node::Lit {
            id: 0,
            var: 1,
            phase: true,
        }],
    };
    assert_eq!(count_unchecked(&c, &declared(&[1])), BigUint::from(1u64));
}

#[test]
fn smoothing_multiplies_missing_vars() {
    // x1 over declared universe {1,2,3}: 2^2 = 4 assignments.
    let c = Circuit {
        root: 0,
        nodes: vec![Node::Lit {
            id: 0,
            var: 1,
            phase: true,
        }],
    };
    let out = count_models(&c, &declared(&[1, 2, 3]), "t-smooth", ValidationProof::default());
    assert_eq!(out.total, BigUint::from(4u64));
    assert_eq!(out.proof.top_smooth_exp, 2);
}

#[test]
fn deterministic_or_with_child_smoothing() {
    // x1 OR (NOT x1 AND x2) over {1,2}: 2 + 1 = 3 assignments.
    // The literal child must be smoothed by 2^1 inside the OR.
    let c = Circuit {
        root: 0,
        nodes: vec![
            Node::Or {
                id: 0,
                children: vec![1, 2],
            },
            Node::Lit {
                id: 1,
                var: 1,
                phase: true,
            },
            Node::And {
                id: 2,
                children: vec![3, 4],
            },
            Node::Lit {
                id: 3,
                var: 1,
                phase: false,
            },
            Node::Lit {
                id: 4,
                var: 2,
                phase: true,
            },
        ],
    };
    assert_eq!(count_validated(&c, &declared(&[1, 2])), BigUint::from(3u64));
}

#[test]
fn and_of_disjoint_ors() {
    // (x1 OR (NOT x1 AND x2)) AND (x3 OR NOT x3) over {1,2,3}: 3 * 2 = 6.
    let c = Circuit {
        root: 0,
        nodes: vec![
            Node::And {
                id: 0,
                children: vec![1, 2],
            },
            Node::Or {
                id: 1,
                children: vec![3, 4],
            },
            Node::Or {
                id: 2,
                children: vec![5, 6],
            },
            Node::Lit {
                id: 3,
                var: 1,
                phase: true,
            },
            Node::And {
                id: 4,
                children: vec![7, 8],
            },
            Node::Lit {
                id: 5,
                var: 3,
                phase: true,
            },
            Node::Lit {
                id: 6,
                var: 3,
                phase: false,
            },
            Node::Lit {
                id: 7,
                var: 1,
                phase: false,
            },
            Node::Lit {
                id: 8,
                var: 2,
                phase: true,
            },
        ],
    };
    assert_eq!(count_validated(&c, &declared(&[1, 2, 3])), BigUint::from(6u64));
}

#[test]
fn big_integer_smoothing() {
    // Constant true over 100 declared variables: exactly 2^100.
    let c = Circuit {
        root: 0,
        nodes: vec![Node::True { id: 0 }],
    };
    let vars: BTreeSet<u32> = (1..=100).collect();
    let out = count_models(&c, &vars, "t-big", ValidationProof::default());
    let expected = BigUint::from(1u64) << 100u32;
    assert_eq!(out.total, expected);
    assert_eq!(out.proof.total, expected.to_string());
}

#[test]
fn big_integer_and_of_tautologies() {
    // AND over i=1..=64 of (x_i OR NOT x_i): every factor contributes 2,
    // so the total is exactly 2^64 = 18446744073709551616.
    let mut nodes = vec![Node::And {
        id: 0,
        children: (1..=64).collect(),
    }];
    for i in 1..=64u32 {
        nodes.push(Node::Or {
            id: i,
            children: vec![100 + i, 200 + i],
        });
        nodes.push(Node::Lit {
            id: 100 + i,
            var: i,
            phase: true,
        });
        nodes.push(Node::Lit {
            id: 200 + i,
            var: i,
            phase: false,
        });
    }
    let c = Circuit { root: 0, nodes };
    let vars: BTreeSet<u32> = (1..=64).collect();
    let expected = BigUint::from(1u64) << 64u32;
    assert_eq!(count_validated(&c, &vars), expected);
}

#[test]
fn big_integer_or_plus_top_smoothing() {
    // (x1 OR NOT x1) over declared universe of 70 vars: 2 * 2^69 = 2^70.
    let c = Circuit {
        root: 0,
        nodes: vec![
            Node::Or {
                id: 0,
                children: vec![1, 2],
            },
            Node::Lit {
                id: 1,
                var: 1,
                phase: true,
            },
            Node::Lit {
                id: 2,
                var: 1,
                phase: false,
            },
        ],
    };
    let vars: BTreeSet<u32> = (1..=70).collect();
    let expected = BigUint::from(1u64) << 70u32;
    assert_eq!(count_validated(&c, &vars), expected);
}
