//! Independent integration tests.
//!
//! Every scenario below is checked three ways:
//!
//! 1. Against **literal expected values** written by hand in this file.
//! 2. Against the separate [`common::oracle`] (a per-outer-row interpreter
//!    that never calls the engine under test).
//! 3. The engine's own response must report `equivalence.equivalent == true`
//!    between its decorrelated executor and its row-by-row interpreter, or an
//!    identical failure category on error paths.
//!
//! Required counter-examples are all present: outer NULL key, duplicate outer
//! keys, empty inner table, and a scalar subquery returning multiple rows.

mod common;

use common::{fixture_json, query_request_json, run_oracle, OracleOutcome, OracleQuery};
use decorr::ast::LoadFixturesRequest;
use decorr::error::FailureCategory;
use decorr::{build_app_state, Catalog, QueryService};
use serde_json::{json, Value};

fn service_with_fixtures() -> QueryService {
    let service = build_app_state();
    let req: LoadFixturesRequest =
        serde_json::from_value(fixture_json()).expect("fixture request deserializes");
    service.load_fixtures(&req).expect("fixtures load");
    service
}

fn run(service: &QueryService, q: &OracleQuery<'_>) -> decorr::ast::QuerySuccess {
    let body = query_request_json(q, "test-query");
    let req = serde_json::from_value(body).expect("query request deserializes");
    service
        .run_query(req)
        .unwrap_or_else(|f| panic!("query unexpectedly failed: {f:?}"))
}

fn run_expect_failure(service: &QueryService, q: &OracleQuery<'_>) -> decorr::ast::QueryFailure {
    let req: decorr::ast::QueryRequest =
        serde_json::from_value(query_request_json(q, "test-query-fail"))
            .expect("request deserializes");
    *service
        .run_query(req)
        .expect_err("query was expected to fail")
}

fn output_column(success: &decorr::ast::QuerySuccess, name: &str) -> Vec<Value> {
    success
        .rows
        .iter()
        .map(|row| row.get(name).cloned().expect("output column present"))
        .collect()
}

#[test]
fn exists_preserves_duplicates_treats_null_as_no_match() {
    let service = service_with_fixtures();
    let q = OracleQuery {
        outer_relation: "orders",
        inner_relation: "payments",
        select: &["o_id", "cust"],
        correlation: &[("cust", "cust")],
        op: "exists",
        aggregate: None,
        value_column: None,
        output_column: "has_payment",
    };

    let success = run(&service, &q);

    // (1) Literal expectation, written by hand.
    let expected = vec![true, true, false, true, true, false];
    let actual: Vec<bool> = output_column(&success, "has_payment")
        .into_iter()
        .map(|v| v.as_bool().unwrap())
        .collect();
    assert_eq!(actual, expected, "EXISTS flags");

    // Outer multiplicity is preserved exactly, including duplicate cust keys.
    assert_eq!(success.rows.len(), 6);
    let o_ids: Vec<Value> = success.rows.iter().map(|r| r["o_id"].clone()).collect();
    assert_eq!(
        o_ids,
        vec![json!(1), json!(2), json!(3), json!(4), json!(5), json!(6)]
    );

    // (2) Independent oracle over the raw JSON.
    match run_oracle(&fixture_json(), &q) {
        OracleOutcome::Rows(oracle_rows) => {
            assert_eq!(success.rows.len(), oracle_rows.len());
            for (got, want) in success.rows.iter().zip(&oracle_rows) {
                assert_eq!(got.as_object().unwrap(), want);
            }
        }
        other => panic!("oracle unexpectedly failed: {other:?}"),
    }

    // (3) Built-in cross-check verdict.
    let verdict = success.equivalence.as_ref().expect("equivalence present");
    assert!(verdict.equivalent, "{verdict:?}");
    assert_eq!(success.primary_executor, "decorrelated");
}

#[test]
fn not_exists_is_anti_join_and_null_outer_key_passes() {
    let service = service_with_fixtures();
    let q = OracleQuery {
        outer_relation: "orders",
        inner_relation: "payments",
        select: &["o_id"],
        correlation: &[("cust", "cust")],
        op: "not_exists",
        aggregate: None,
        value_column: None,
        output_column: "unpaid",
    };
    let success = run(&service, &q);
    let actual: Vec<bool> = output_column(&success, "unpaid")
        .into_iter()
        .map(|v| v.as_bool().unwrap())
        .collect();
    // NULL outer key finds no match -> NOT EXISTS is TRUE.
    assert_eq!(actual, vec![false, false, true, false, false, true]);
    if let OracleOutcome::Rows(rows) = run_oracle(&fixture_json(), &q) {
        for (got, want) in success.rows.iter().zip(&rows) {
            assert_eq!(got.as_object().unwrap(), want);
        }
    } else {
        panic!("oracle failure");
    }
    assert!(success.equivalence.as_ref().unwrap().equivalent);
}

#[test]
fn count_vs_sum_differ_on_empty_and_all_null_groups() {
    let service = service_with_fixtures();
    let base = OracleQuery {
        outer_relation: "orders",
        inner_relation: "payments",
        select: &["o_id"],
        correlation: &[("cust", "cust")],
        op: "scalar_aggregate",
        aggregate: Some("count_star"),
        value_column: None,
        output_column: "cnt",
    };
    let counts = run(&service, &base);
    // cust: 10,10,NULL,20,20,99 -> group cardinalities
    // 20 has one inner row whose amount is NULL: COUNT(*) still 1.
    assert_eq!(
        output_column(&counts, "cnt"),
        vec![json!(2), json!(2), json!(0), json!(1), json!(1), json!(0)]
    );
    assert_eq!(
        counts.columns.last().unwrap().r#type,
        "INTEGER",
        "COUNT(*) is INTEGER even over an empty group"
    );

    let sums_q = OracleQuery {
        op: "scalar_aggregate",
        aggregate: Some("sum"),
        value_column: Some("amount"),
        output_column: "total",
        ..base
    };
    let sums = run(&service, &sums_q);
    // 10 -> 150; NULL outer -> NULL; 20 -> NULL (group exists, only value NULL);
    // 99 -> NULL (empty group). COUNT and SUM therefore disagree on row 3.
    assert_eq!(
        output_column(&sums, "total"),
        vec![
            json!(150),
            json!(150),
            Value::Null,
            Value::Null,
            Value::Null,
            Value::Null
        ]
    );

    if let OracleOutcome::Rows(rows) = run_oracle(&fixture_json(), &sums_q) {
        assert_eq!(sums.rows.len(), rows.len());
        for (got, want) in sums.rows.iter().zip(&rows) {
            assert_eq!(got.as_object().unwrap(), want);
        }
    } else {
        panic!("oracle failure");
    }
    assert!(sums.equivalence.as_ref().unwrap().equivalent);

    // Explicit semantic contrast on the same correlated group (outer row 3):
    let row_20_count = counts.rows[3].get("cnt").unwrap();
    let row_20_sum = sums.rows[3].get("total").unwrap();
    assert_eq!(row_20_count, &json!(1));
    assert_eq!(row_20_sum, &Value::Null);
}

#[test]
fn empty_inner_table_is_handled_by_all_supported_ops() {
    let service = service_with_fixtures();
    let ops: Vec<(&str, Option<&str>, Option<&str>, Value)> = vec![
        ("exists", None, None, json!(false)),
        ("not_exists", None, None, json!(true)),
        ("scalar_aggregate", Some("count_star"), None, json!(0)),
        ("scalar_aggregate", Some("sum"), Some("amount"), Value::Null),
        ("scalar", None, Some("amount"), Value::Null),
    ];
    for (op, agg, value_col, expected) in ops {
        let q = OracleQuery {
            outer_relation: "orders",
            inner_relation: "payments_empty",
            select: &["o_id"],
            correlation: &[("cust", "cust")],
            op,
            aggregate: agg,
            value_column: value_col,
            output_column: "v",
        };
        let success = run(&service, &q);
        assert_eq!(success.rows.len(), 6, "{op} keeps all outer rows");
        for row in &success.rows {
            assert_eq!(row.get("v").unwrap(), &expected, "op={op}");
        }
        assert!(success.equivalence.as_ref().unwrap().equivalent, "op={op}");
        if let OracleOutcome::Rows(rows) = run_oracle(&fixture_json(), &q) {
            assert_eq!(rows.len(), success.rows.len(), "oracle {op}");
        } else {
            panic!("oracle failed for {op}");
        }
    }
}

#[test]
fn scalar_multi_row_group_raises_cardinality_violation_for_both_executors() {
    let service = service_with_fixtures();
    // payments has TWO rows for cust=10: scalar lookup must fail.
    let q = OracleQuery {
        outer_relation: "orders",
        inner_relation: "payments",
        select: &["o_id"],
        correlation: &[("cust", "cust")],
        op: "scalar",
        aggregate: None,
        value_column: Some("p_id"),
        output_column: "one_payment",
    };

    let failure = run_expect_failure(&service, &q);
    assert_eq!(
        failure.failure.category,
        FailureCategory::CardinalityViolation.code()
    );
    assert!(failure.failure.message.contains("2 rows"));
    assert!(
        failure.failure.message.contains("outer row 0"),
        "{}",
        failure.failure.message
    );
    // Independent interpreter must independently reach the SAME category.
    assert_eq!(failure.failure_cross_check, Some(true));

    match run_oracle(&fixture_json(), &q) {
        OracleOutcome::Failure {
            category,
            outer_row,
        } => {
            assert_eq!(category, "scalar_cardinality_violation");
            assert_eq!(outer_row, Some(0));
        }
        other => panic!("oracle should have rejected multi-row scalar, got {other:?}"),
    }
}

#[test]
fn scalar_single_row_lookup_returns_value_else_null() {
    let service = service_with_fixtures();
    let q = OracleQuery {
        outer_relation: "orders",
        inner_relation: "labels",
        select: &["o_id", "cust"],
        correlation: &[("cust", "cust")],
        op: "scalar",
        aggregate: None,
        value_column: Some("label"),
        output_column: "lbl",
    };
    let success = run(&service, &q);
    assert_eq!(
        output_column(&success, "lbl"),
        vec![
            json!("a"),
            json!("a"),
            Value::Null, // outer NULL
            json!("b"),
            json!("b"),
            Value::Null, // no match
        ]
    );
    assert_eq!(success.columns.last().unwrap().r#type, "TEXT");
    assert!(success.equivalence.as_ref().unwrap().equivalent);
}

#[test]
fn not_in_is_rejected_as_unsupported_and_explains_null_difference() {
    let service = service_with_fixtures();
    let q = OracleQuery {
        outer_relation: "orders",
        inner_relation: "payments",
        select: &["o_id"],
        correlation: &[("cust", "cust")],
        op: "not_in",
        aggregate: None,
        value_column: None,
        output_column: "x",
    };
    let failure = run_expect_failure(&service, &q);
    assert_eq!(
        failure.failure.category,
        FailureCategory::UnsupportedForm.code()
    );
    assert_eq!(failure.failure.unsupported_form.as_deref(), Some("NOT IN"));
    let reason = failure.failure.reason.as_deref().unwrap_or_default();
    assert!(
        reason.contains("NULL"),
        "reason must explain NULL semantics: {reason}"
    );
    assert!(reason.contains("NOT EXISTS"));
    assert!(failure.failure.remediation.is_some());
    // Rejection happens before any equivalence claim could be made.
    assert_eq!(failure.failure_cross_check, None);

    // IN / ANY / ALL are rejected with the same category.
    for op in ["in", "any", "all"] {
        let q2 = OracleQuery { op, ..q };
        let f2 = run_expect_failure(&service, &q2);
        assert_eq!(
            f2.failure.category,
            FailureCategory::UnsupportedForm.code(),
            "op={op}"
        );
    }
}

#[test]
fn not_in_and_not_exists_actually_diverge_under_null() {
    // Demonstration computed entirely inside the test (no engine code): with
    // inner key list [10, 10, 20, NULL], NOT IN yields UNKNOWN for EVERY outer
    // value, while NOT EXISTS keeps cust=99. This is why the rewrite is
    // refused rather than applied silently.
    let inner_list: Vec<Option<i64>> = vec![Some(10), Some(10), Some(20), None];
    let outer_values: Vec<Option<i64>> = vec![Some(10), None, Some(99)];

    // Three-valued NOT IN: Some(false)=FALSE, None=UNKNOWN, Some(true)=TRUE.
    let not_in: Vec<Option<bool>> = outer_values
        .iter()
        .map(|x| match x {
            None => None, // NULL NOT IN (...) is UNKNOWN
            Some(xv) => {
                // Conjunction of x <> y_i under three-valued logic:
                // equality -> definite FALSE; a NULL with no equality -> UNKNOWN.
                let mut result = Some(true);
                for y in &inner_list {
                    match y {
                        Some(yv) if xv == yv => {
                            result = Some(false);
                            break; // FALSE AND anything = FALSE
                        }
                        None => result = None,
                        _ => {}
                    }
                }
                result
            }
        })
        .collect();

    // Correlated NOT EXISTS: TRUE iff no equal non-NULL inner record exists.
    let not_exists: Vec<bool> = outer_values
        .iter()
        .map(|x| match x {
            None => true,
            Some(xv) => !inner_list.iter().any(|y| y.as_ref() == Some(xv)),
        })
        .collect();

    // x=10 is FALSE in both; but outer NULL is UNKNOWN vs TRUE, and x=99 is
    // UNKNOWN (NULL in list) vs TRUE — the direct divergence witnesses.
    assert_eq!(not_in, vec![Some(false), None, None], "NOT IN result");
    assert_eq!(not_exists, vec![false, true, true], "NOT EXISTS result");
    assert_ne!(not_in[1], Some(not_exists[1]));
    assert_ne!(not_in[2], Some(not_exists[2]));
}

#[test]
fn service_is_usable_from_a_fresh_catalog() {
    // Guards against hard-coded demo data inside the engine.
    let fresh = QueryService::new(Catalog::new());
    let q = OracleQuery {
        outer_relation: "orders",
        inner_relation: "payments",
        select: &["o_id"],
        correlation: &[("cust", "cust")],
        op: "exists",
        aggregate: None,
        value_column: None,
        output_column: "v",
    };
    let body = query_request_json(&q, "fresh");
    let req = serde_json::from_value(body).unwrap();
    let failure = fresh
        .run_query(req)
        .expect_err("missing relation must fail");
    assert_eq!(failure.failure.category, FailureCategory::Schema.code());
}
