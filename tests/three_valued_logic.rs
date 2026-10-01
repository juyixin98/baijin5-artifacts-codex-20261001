//! Independent tests for NULL crossings, all-UNKNOWN predicates, IS NULL and
//! the "every row is in exactly one of TRUE/FALSE/UNKNOWN" partition property.
//!
//! Expected answers come from the hand-written scalar oracle in `common`, never
//! from the bitmap engine.

mod common;

use common::{cmp, eval_scalar, init_tracing, intv, people_spec, rows_to_json, txtv, Ora, OraRow};
use tribool_index::logic::Tri3;
use tribool_index::query::{run, Expr};
use tribool_index::state::Catalog;

/// All nine scalar (operand A x operand B) NULL-crossing patterns appear here.
fn crossing_rows() -> Vec<OraRow> {
    vec![
        // age pattern: non-null / null; active pattern via separate rows below
        OraRow {
            city: Some("BJ".into()),
            age: Some(10),
            score: Some(1),
            active: Some(true),
        },
        OraRow {
            city: Some("BJ".into()),
            age: None,
            score: Some(2),
            active: Some(false),
        },
        OraRow {
            city: Some("SH".into()),
            age: Some(20),
            score: None,
            active: None,
        },
        OraRow {
            city: None,
            age: Some(10),
            score: Some(4),
            active: Some(true),
        },
        OraRow {
            city: Some("SH".into()),
            age: Some(30),
            score: None,
            active: Some(false),
        },
        OraRow {
            city: None,
            age: None,
            score: Some(6),
            active: None,
        },
        OraRow {
            city: Some("BJ".into()),
            age: Some(99),
            score: None,
            active: Some(true),
        },
        OraRow {
            city: None,
            age: Some(20),
            score: None,
            active: Some(false),
        },
        OraRow {
            city: Some("GZ".into()),
            age: None,
            score: Some(9),
            active: None,
        },
        // 10th row makes a non-multiple-of-8 universe together with the rest.
        OraRow {
            city: Some("BJ".into()),
            age: Some(20),
            score: Some(0),
            active: Some(true),
        },
        OraRow {
            city: Some("GZ".into()),
            age: Some(5),
            score: None,
            active: Some(false),
        },
    ]
}

fn table_with(rows: &[OraRow]) -> std::sync::Arc<std::sync::RwLock<tribool_index::state::Table>> {
    let mut catalog = Catalog::new();
    catalog
        .create_table("t", people_spec(), rows_to_json(rows))
        .unwrap()
}

fn match_ora(expected: Ora, got: Option<Tri3>) -> bool {
    matches!(
        (expected, got),
        (Ora::T, Some(Tri3::True)) | (Ora::F, Some(Tri3::False)) | (Ora::U, Some(Tri3::Unknown))
    )
}

/// Exhaustively compare engine vs oracle, row by row, and verify partition size.
fn assert_expr_matches_oracle(rows: &[OraRow], expr: &Expr, run_id: &str) {
    let table = table_with(rows);
    let result = {
        let t = table.read().unwrap();
        run(&t, expr, run_id).unwrap()
    };
    let mut t_count = 0usize;
    let mut f_count = 0usize;
    let mut u_count = 0usize;
    for (i, row) in rows.iter().enumerate() {
        let expected = eval_scalar(expr, row);
        let got = result.tris.state_at(i);
        assert!(
            match_ora(expected, got),
            "{run_id}: row {i} expected {expected:?} got {got:?} for expr {expr:?}"
        );
        match got.unwrap() {
            Tri3::True => t_count += 1,
            Tri3::False => f_count += 1,
            Tri3::Unknown => u_count += 1,
        }
    }
    assert_eq!(t_count, result.tris.count_true(), "{run_id}: TRUE count");
    assert_eq!(f_count, result.tris.count_false(), "{run_id}: FALSE count");
    assert_eq!(
        u_count,
        result.tris.count_unknown(),
        "{run_id}: UNKNOWN count"
    );
    assert_eq!(
        t_count + f_count + u_count,
        rows.len(),
        "{run_id}: every row must belong to exactly one set"
    );
}

#[test]
fn null_crossings_for_comparison_operators() {
    init_tracing();
    let rows = crossing_rows();
    let run_id = "test-null-crossings-comparisons";
    let exprs = vec![
        cmp("age", "=", intv(10)),
        cmp("age", "!=", intv(10)),
        cmp("age", "<", intv(20)),
        cmp("age", "<=", intv(20)),
        cmp("age", ">", intv(10)),
        cmp("age", ">=", intv(20)),
        cmp("city", "=", txtv("BJ")),
        cmp("active", "=", common::boolv(true)),
    ];
    for expr in exprs {
        tracing::info!(run_id, expr = ?expr, "comparing against scalar oracle");
        assert_expr_matches_oracle(&rows, &expr, run_id);
    }
}

#[test]
fn null_crossings_for_and_or_not_kleene_matrix() {
    init_tracing();
    let rows = crossing_rows();
    let run_id = "test-kleene-matrix";
    // Build expressions that force every (T/F/U) x (T/F/U) combination by using
    // two independently-nullable columns.
    let exprs: Vec<Expr> = vec![
        Expr::And {
            args: vec![
                cmp("age", ">=", intv(10)),
                cmp("active", "=", common::boolv(true)),
            ],
        },
        Expr::Or {
            args: vec![
                cmp("age", ">", intv(50)),
                cmp("active", "=", common::boolv(false)),
            ],
        },
        Expr::Not {
            arg: Box::new(cmp("age", "=", intv(20))),
        },
        Expr::And {
            args: vec![
                Expr::Not {
                    arg: Box::new(cmp("city", "=", txtv("BJ"))),
                },
                cmp("score", ">", intv(0)),
            ],
        },
        Expr::Or {
            args: vec![
                cmp("age", "=", intv(99)),
                Expr::Not {
                    arg: Box::new(cmp("active", "=", common::boolv(true))),
                },
            ],
        },
        // Nested: NOT (A AND B) vs per-row oracle.
        Expr::Not {
            arg: Box::new(Expr::And {
                args: vec![
                    cmp("age", "<", intv(50)),
                    cmp("active", "=", common::boolv(true)),
                ],
            }),
        },
    ];
    for expr in exprs {
        tracing::info!(run_id, expr = ?expr, "Kleene matrix check");
        assert_expr_matches_oracle(&rows, &expr, run_id);
    }
}

#[test]
fn all_unknown_predicate_classifies_every_row_unknown_not_false() {
    init_tracing();
    let run_id = "test-all-unknown";
    // Every age is NULL in this fixture.
    let rows: Vec<OraRow> = (0..13)
        .map(|i| OraRow {
            city: Some("X".into()),
            age: None,
            score: Some(i),
            active: Some(true),
        })
        .collect();
    let table = table_with(&rows);
    let expr = cmp("age", "=", intv(1));
    let result = {
        let t = table.read().unwrap();
        run(&t, &expr, run_id).unwrap()
    };
    assert_eq!(result.tris.count_true(), 0, "all-NULL compare -> no TRUE");
    assert_eq!(
        result.tris.count_false(),
        0,
        "all-NULL compare -> no FALSE (must not collapse UNKNOWN to FALSE)"
    );
    assert_eq!(result.tris.count_unknown(), 13, "all 13 rows UNKNOWN");
    // The same under NOT: NOT UNKNOWN is still UNKNOWN, never TRUE.
    let not_expr = Expr::Not {
        arg: Box::new(expr),
    };
    let r2 = {
        let t = table.read().unwrap();
        run(&t, &not_expr, run_id).unwrap()
    };
    assert_eq!(r2.tris.count_true(), 0);
    assert_eq!(r2.tris.count_false(), 0);
    assert_eq!(r2.tris.count_unknown(), 13);
    tracing::info!(
        run_id,
        true_cnt = 0,
        false_cnt = 0,
        unknown_cnt = 13,
        "all-unknown verified; NOT leaves UNKNOWN unchanged"
    );
}

#[test]
fn is_null_and_is_not_null_are_exact_complements_over_alive_rows() {
    init_tracing();
    let run_id = "test-is-null";
    let rows = crossing_rows();
    let table = table_with(&rows);
    let null_expr = cmp("age", "is_null", None);
    let notnull_expr = cmp("age", "is_not_null", None);

    let (rn, rnn) = {
        let t = table.read().unwrap();
        (
            run(&t, &null_expr, run_id).unwrap(),
            run(&t, &notnull_expr, run_id).unwrap(),
        )
    };
    // IS NULL has no UNKNOWN rows (it is a null-intolerant test, not a compare).
    assert_eq!(rn.tris.count_unknown(), 0);
    assert_eq!(rnn.tris.count_unknown(), 0);
    for (i, row) in rows.iter().enumerate() {
        assert_eq!(
            rn.tris.state_at(i).unwrap(),
            if row.age.is_none() {
                Tri3::True
            } else {
                Tri3::False
            }
        );
        assert_eq!(
            rnn.tris.state_at(i).unwrap(),
            if row.age.is_some() {
                Tri3::True
            } else {
                Tri3::False
            }
        );
        assert!(match_ora(eval_scalar(&null_expr, row), rn.tris.state_at(i)));
        assert!(match_ora(
            eval_scalar(&notnull_expr, row),
            rnn.tris.state_at(i)
        ));
    }
    // Exact complement: TRUE(is null) == FALSE(is not null), counts sum to N.
    assert_eq!(
        rn.tris.count_true(),
        3,
        "NULL-age rows are fixture rows 1,5,8"
    );
    assert_eq!(rn.tris.count_true() + rnn.tris.count_true(), rows.len());
}

#[test]
fn empty_and_or_are_rejected_not_silently_true_or_false() {
    init_tracing();
    let rows = crossing_rows();
    let table = table_with(&rows);
    let t = table.read().unwrap();
    let err = run(&t, &Expr::And { args: vec![] }, "empty-and").unwrap_err();
    assert_eq!(err.kind, tribool_index::error::ErrorKind::InvalidInput);
    let err = run(&t, &Expr::Or { args: vec![] }, "empty-or").unwrap_err();
    assert_eq!(err.kind, tribool_index::error::ErrorKind::InvalidInput);
}
