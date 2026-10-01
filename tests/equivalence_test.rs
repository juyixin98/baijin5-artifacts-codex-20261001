//! Independent equivalence tests.
//!
//! These run the *full* pipeline on the wire JSON, in the default
//! `crosscheck` mode (row-at-a-time reference vs decorrelated group/join), and
//! assert hand-derived concrete answers. Expected rows were computed from the
//! fixture definitions in `common/mod.rs`, not from the engine.

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
    let v = serde_json::to_value(&outcome).unwrap();
    // Surface the real HTTP status next to the body for precise assertions.
    serde_json::json!({ "http_status": code, "outcome": v })
}

fn ids(env: &Value) -> Vec<Option<i64>> {
    id_rows(&env["outcome"])
}

fn assert_ok(env: &Value) -> &Value {
    assert_eq!(env["http_status"], 200, "expected 200, got: {env}");
    assert_eq!(env["outcome"]["status"], "ok", "got: {env}");
    assert_eq!(
        env["outcome"]["crosscheck"]["match"], true,
        "engines must agree: {env}"
    );
    &env["outcome"]
}

#[test]
fn exists_keeps_duplicate_keys_and_rejects_null_outer_key() {
    // cust 10 appears for outer rows 1,3,7 -> all kept (multiplicity);
    // cust 20 -> row 2; NULL corr key (row 4), cust 30/40 -> no group.
    let body = request(json!([exists_term("lines", "l", false)]));
    let env = run(body);
    assert_ok(&env);
    assert_eq!(ids(&env), vec![Some(1), Some(2), Some(3), Some(7)]);
    assert_eq!(env["outcome"]["result"]["row_count"], 4);
}

#[test]
fn not_exists_is_the_anti_join_including_null_key_and_empty_inner() {
    // Negation of EXISTS: NULL outer key and absent groups survive.
    let body = request(json!([exists_term("lines", "l", true)]));
    let env = run(body);
    assert_ok(&env);
    assert_eq!(ids(&env), vec![Some(4), Some(5), Some(6)]);

    // Empty inner table: every outer row anti-matches, multiplicity preserved.
    let body = request(json!([exists_term("empty_lines", "l", true)]));
    let env = run(body);
    assert_ok(&env);
    assert_eq!(
        ids(&env),
        vec![
            Some(1),
            Some(2),
            Some(3),
            Some(4),
            Some(5),
            Some(6),
            Some(7)
        ]
    );
}

#[test]
fn exists_over_empty_inner_matches_nothing() {
    let body = request(json!([exists_term("empty_lines", "l", false)]));
    let env = run(body);
    assert_ok(&env);
    assert_eq!(ids(&env), Vec::<Option<i64>>::new());
}

#[test]
fn count_on_correlated_and_empty_groups_returns_zero() {
    // COUNT(amt) >= want:
    //  cust10 group amts 5,7 -> count 2
    //  cust20 group [NULL]   -> count 0 (NULL ignored, group nonempty)
    //  cust30/40 absent      -> count 0 (empty group)
    //  NULL outer key        -> no matched group -> count 0
    // Rows kept (want >= count): 2 (2>=0), 4 (1>=0), 5,6 (5>=0), 7 (999>=2).
    let term = agg_term("lines", "l", "count", "amt", "ge", col("want"));
    let env = run(request(json!([term])));
    assert_ok(&env);
    assert_eq!(ids(&env), vec![Some(2), Some(4), Some(5), Some(6), Some(7)]);
}

#[test]
fn sum_differs_from_count_on_empty_and_null_only_groups() {
    // SUM(amt) >= want, same fixtures as the COUNT test.
    //  cust10 -> 12 ; cust20 (only NULL) -> NULL ; absent -> NULL.
    // NULL (3VL) rejects rows 2,4,5,6; only row 7 (999 >= 12) survives.
    let term = agg_term("lines", "l", "sum", "amt", "ge", col("want"));
    let env = run(request(json!([term])));
    assert_ok(&env);
    assert_eq!(ids(&env), vec![Some(7)]);
}

#[test]
fn sum_explicitly_returns_null_on_an_empty_inner_table() {
    // SUM over an empty inner is NULL for every outer row -> all comparisons
    // UNKNOWN -> no rows. COUNT over the same table is 0 and would keep rows,
    // proving the COUNT/SUM-on-empty distinction end to end.
    let sum_term = agg_term("empty_lines", "l", "sum", "amt", "ge", col("want"));
    let env = run(request(json!([sum_term])));
    assert_ok(&env);
    assert_eq!(ids(&env), Vec::<Option<i64>>::new());

    let count_term = agg_term("empty_lines", "l", "count", "amt", "ge", col("want"));
    let env = run(request(json!([count_term])));
    assert_ok(&env);
    // want >= 0 for every (non-NULL want) outer row.
    assert_eq!(
        ids(&env),
        vec![
            Some(1),
            Some(2),
            Some(3),
            Some(4),
            Some(5),
            Some(6),
            Some(7)
        ]
    );
}

#[test]
fn in_is_null_aware_within_the_matched_group() {
    // want IN (SELECT l.code ...):
    //  cust10 projects [1, NULL]: rows 1,3 match on 1; row 7 (want 999)
    //    finds no equal but sees NULL -> UNKNOWN -> rejected (the NULL trap);
    //  cust20 projects [2]: row 2 matches;
    //  NULL outer key / absent group -> FALSE.
    let term = in_term("lines", "l", "code", col("want"), false);
    let env = run(request(json!([term])));
    assert_ok(&env);
    assert_eq!(ids(&env), vec![Some(1), Some(2), Some(3)]);
}

#[test]
fn in_over_empty_inner_is_false_for_everyone() {
    let term = in_term("empty_lines", "l", "code", col("want"), false);
    let env = run(request(json!([term])));
    assert_ok(&env);
    assert_eq!(ids(&env), Vec::<Option<i64>>::new());
}

#[test]
fn in_with_null_outer_value_is_unknown() {
    // Compare a NULL outer expression against the projection: always UNKNOWN.
    let null_lit = json!({"kind": "literal", "type": "int", "value": null});
    let term = in_term("lines", "l", "code", null_lit, false);
    let env = run(request(json!([term])));
    assert_ok(&env);
    assert_eq!(ids(&env), Vec::<Option<i64>>::new());
}

#[test]
fn bare_scalar_single_row_group_preserves_duplicates() {
    // Restrict to cust 10 outer rows; scalar_src has exactly one code (9) for
    // cust 10. 9 = 9 keeps the three duplicate-key rows.
    let scalar = json!({
        "kind": "scalar_sub",
        "outer": int_lit(9),
        "op": "eq",
        "sub": sub("scalar_src", "s",
            json!([corr("o", "cust", "s", "cust")]),
            json!({"project": qcol("s", "code")}))
    });
    let local =
        json!({"kind": "local", "op": "eq", "left": qcol("o", "cust"), "right": int_lit(10)});
    let env = run(request(json!([local, scalar])));
    assert_ok(&env);
    assert_eq!(ids(&env), vec![Some(1), Some(3), Some(7)]);
}

#[test]
fn bare_scalar_multiple_rows_is_an_error_in_both_engines() {
    // scalar_src cust 40 projects codes 5 and 6 -> outer row 6 hits a group of
    // cardinality 2. Both engines must raise scalar_multiple_rows (HTTP 422).
    let scalar = json!({
        "kind": "scalar_sub",
        "outer": col("want"),
        "op": "eq",
        "sub": sub("scalar_src", "s",
            json!([corr("o", "cust", "s", "cust")]),
            json!({"project": qcol("s", "code")}))
    });
    let env = run(request(json!([scalar])));

    assert_eq!(env["http_status"], 422, "expected 422, got: {env}");
    assert_eq!(env["outcome"]["status"], "error");
    let err = &env["outcome"]["errors"][0];
    assert_eq!(err["kind"], "scalar_multiple_rows");
    assert_eq!(err["engine"], "naive+rewrite");
    // The equivalence check records agreement even on the failure category.
    assert_eq!(env["outcome"]["crosscheck"]["match"], true);
    assert!(env["outcome"]["result"].is_null());
}

#[test]
fn both_engines_agree_when_run_individually_too() {
    for mode in ["naive", "rewrite"] {
        let mut body = request(json!([exists_term("lines", "l", false)]));
        body["options"] = json!({"mode": mode});
        let env = run(body);
        assert_eq!(env["http_status"], 200);
        assert_eq!(
            ids(&env),
            vec![Some(1), Some(2), Some(3), Some(7)],
            "mode {mode}"
        );
    }
}

#[test]
fn arrow_types_and_request_identity_are_explained() {
    let body = request(json!([exists_term("lines", "l", false)]));
    let env = run(body);
    let out = assert_ok(&env);
    assert_eq!(out["request_id"], request_id());
    assert_eq!(out["result"]["arrow_types"], serde_json::json!(["Int64"]));
    assert_eq!(out["result"]["multiplicity_preserved"], true);
    let step_names: Vec<&str> = out["steps"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s["step"].as_str().unwrap())
        .collect();
    for required in [
        "build_catalog",
        "validate",
        "compile_plan",
        "execute_naive",
        "execute_rewrite",
        "crosscheck",
        "arrow_encode",
    ] {
        assert!(
            step_names.contains(&required),
            "missing step {required}: {step_names:?}"
        );
    }
}
