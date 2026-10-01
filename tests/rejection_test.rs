//! Rejection and validation tests.
//!
//! Unsupported or malformed forms must be refused with a precise error kind
//! rather than silently mis-compiled. These assert the failure *category*.

mod common;

use decorrelate_svc::api::{run_pipeline, RawRequest};
use decorrelate_svc::config::AppConfig;
use decorrelate_svc::state::{AppState, RequestCtx};
use serde_json::{json, Value};

use common::*;

fn run(body: Value) -> Value {
    let raw: RawRequest = serde_json::from_value(body).expect("request must deserialize");
    let state = AppState::new(AppConfig::default());
    let ctx = RequestCtx::with_id(request_id());
    let (outcome, code) = run_pipeline(raw, &state, &ctx);
    serde_json::json!({
        "http_status": code,
        "kind": outcome.errors.first().map(|e| e.kind.clone()),
        "at_step": outcome.errors.first().map(|e| e.at_step.clone()),
        "outcome": serde_json::to_value(&outcome).unwrap(),
    })
}

#[test]
fn not_in_is_rejected_as_distinct_from_not_exists() {
    // negated IN is exactly the form whose NULL semantics differ.
    let term = in_term("lines", "l", "code", col("want"), true);
    let env = run(request(json!([term])));
    assert_eq!(env["http_status"], 400);
    assert_eq!(env["kind"], "unsupported_form");
    assert_eq!(env["at_step"], "validate");
    let msg = env["outcome"]["errors"][0]["message"].as_str().unwrap();
    assert!(msg.contains("NOT IN"), "message should name NOT IN: {msg}");
}

#[test]
fn uncorrelated_subquery_is_rejected() {
    // Inner term with no correlation -> outside the supported fragment.
    let sub = sub(
        "lines",
        "l",
        json!([{
            "kind": "local",
            "op": "gt",
            "left": qcol("l", "amt"),
            "right": int_lit(0)
        }]),
        json!({}),
    );
    let term = json!({"kind": "exists", "negated": false, "sub": sub});
    let env = run(request(json!([term])));
    assert_eq!(env["kind"], "correlation_mismatch");
}

#[test]
fn correlation_across_wrong_binding_is_rejected() {
    // Both sides qualify as the outer binding.
    let bad_corr = json!({
        "kind": "correlated",
        "left": qcol("o", "cust"),
        "right": qcol("o", "cust")
    });
    let sub = json!({
        "from": {"relation": "lines", "alias": "l"},
        "where_terms": [bad_corr]
    });
    let term = json!({"kind": "exists", "negated": false, "sub": sub});
    let env = run(request(json!([term])));
    assert_eq!(env["kind"], "correlation_mismatch");
}

#[test]
fn type_mismatch_in_correlation_is_rejected() {
    // Correlate int outer cust with str inner tag.
    let sub = sub(
        "lines",
        "l",
        json!([corr("o", "cust", "l", "tag")]),
        json!({}),
    );
    let term = json!({"kind": "exists", "negated": false, "sub": sub});
    let env = run(request(json!([term])));
    assert_eq!(env["kind"], "correlation_mismatch");
}

#[test]
fn unknown_relation_is_rejected() {
    let term = exists_term("does_not_exist", "l", false);
    let env = run(request(json!([term])));
    assert_eq!(env["kind"], "unknown_reference");
}

#[test]
fn unknown_select_column_is_rejected() {
    let mut q = query(json!([exists_term("lines", "l", false)]));
    q["select"] = json!([{"column": "nope"}]);
    let env = run(request_with_query(q));
    assert_eq!(env["kind"], "unknown_reference");
}

#[test]
fn query_without_subquery_is_rejected() {
    let local = json!({"kind": "local", "op": "eq", "left": col("want"), "right": int_lit(1)});
    let env = run(request(json!([local])));
    assert_eq!(env["kind"], "unsupported_form");
}

#[test]
fn two_subqueries_are_rejected() {
    let terms = json!([
        exists_term("lines", "l", false),
        exists_term("lines", "l", true)
    ]);
    let env = run(request(terms));
    assert_eq!(env["kind"], "unsupported_form");
}

#[test]
fn aggregate_shape_mismatch_is_rejected() {
    // EXISTS must not carry an aggregate.
    let sub = sub(
        "lines",
        "l",
        json!([corr("o", "cust", "l", "cust")]),
        json!({"aggregate": {"func": "count", "column": qcol("l", "amt")}}),
    );
    let term = json!({"kind": "exists", "negated": false, "sub": sub});
    let env = run(request(json!([term])));
    assert_eq!(env["kind"], "unsupported_form");
}

#[test]
fn sum_of_strings_is_type_mismatch() {
    let term = agg_term("lines", "l", "sum", "tag", "ge", col("want"));
    let env = run(request(json!([term])));
    assert_eq!(env["kind"], "type_mismatch");
}

#[test]
fn malformed_json_body_is_invalid_request() {
    // Directly exercise the HTTP-independent deserializer path classification.
    let bad: Value = json!({"query": {"select": "not-a-list"}});
    let err = serde_json::from_value::<RawRequest>(bad);
    assert!(err.is_err(), "malformed body must fail deserialization");
}

#[test]
fn scalar_comparison_type_mismatch_is_rejected() {
    // Outer string vs integer COUNT result.
    let term = agg_term(
        "lines",
        "l",
        "count",
        "amt",
        "eq",
        json!({"kind": "literal", "type": "str", "value": "x"}),
    );
    let env = run(request(json!([term])));
    assert_eq!(env["kind"], "type_mismatch");
}
