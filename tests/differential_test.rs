//! Randomized differential testing.
//!
//! Generates many random outer/inner tables (duplicates, NULL correlation
//! keys, NULL aggregate arguments, empty groups, multi-row scalar groups) and
//! asserts the row-at-a-time reference and the decorrelated executor agree:
//! either identical ordered batches or the identical error category.
//!
//! Absolute correctness is additionally anchored by the hand-derived fixtures
//! in `equivalence_test.rs`; this module hunts for *divergence* between the two
//! independent engines.

mod common;

use decorrelate_svc::api::{batches_equal, run_pipeline, RawRequest};
use decorrelate_svc::config::AppConfig;
use decorrelate_svc::state::{AppState, RequestCtx};
use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};
use serde_json::{json, Value};

use common::*;

struct Gen {
    rng: StdRng,
}

impl Gen {
    fn new(seed: u64) -> Self {
        Self {
            rng: StdRng::seed_from_u64(seed),
        }
    }

    /// Correlation value drawn from a small domain that forces duplicates and
    /// mismatches; ~1/6 NULL.
    fn corr_val(&mut self) -> Option<i64> {
        let x: u32 = self.rng.gen_range(0..12);
        match x {
            0 | 1 => None,
            2..=5 => Some(1),
            6..=8 => Some(2),
            9..=10 => Some(3),
            _ => Some(4),
        }
    }

    fn small_int(&mut self) -> Option<i64> {
        if self.rng.gen_bool(0.2) {
            None
        } else {
            Some(self.rng.gen_range(-4..=4))
        }
    }

    fn code_val(&mut self) -> Option<i64> {
        if self.rng.gen_bool(0.25) {
            None
        } else {
            Some(self.rng.gen_range(1..=3))
        }
    }
}

fn make_fixtures(g: &mut Gen, outer_n: usize, inner_n: usize) -> Value {
    let mut outer_rows = Vec::new();
    for id in 1..=outer_n as i64 {
        let cust = g.corr_val();
        let want = g.small_int().unwrap_or(0);
        outer_rows.push(json!([id, cust, want]));
    }

    let mut inner_rows = Vec::new();
    for lid in 1..=inner_n as i64 {
        let cust = g.corr_val();
        let amt = g.small_int();
        let code = g.code_val();
        let tag = "x";
        inner_rows.push(json!([lid, cust, amt, code, tag]));
    }

    json!({
        "relations": [
            {
                "name": "orders",
                "columns": [
                    {"name": "o_id", "type": "int"},
                    {"name": "cust", "type": "int"},
                    {"name": "want", "type": "int"}
                ],
                "rows": outer_rows
            },
            {
                "name": "lines",
                "columns": [
                    {"name": "l_id", "type": "int"},
                    {"name": "cust", "type": "int"},
                    {"name": "amt", "type": "int"},
                    {"name": "code", "type": "int"},
                    {"name": "tag", "type": "str"}
                ],
                "rows": inner_rows
            }
        ]
    })
}

fn run_body(body: Value) -> Result<Vec<Vec<Option<i64>>>, String> {
    let raw: RawRequest = serde_json::from_value(body).unwrap();
    let state = AppState::new(AppConfig::default());
    let ctx = RequestCtx::with_id("diff");
    let (outcome, _code) = run_pipeline(raw, &state, &ctx);
    if outcome.status == "ok" {
        Ok(outcome
            .result
            .unwrap()
            .rows
            .iter()
            .map(|r| r.iter().map(|c| c.as_i64()).collect::<Vec<_>>())
            .collect())
    } else {
        Err(outcome.errors[0].kind.clone())
    }
}

#[test]
fn engines_never_diverge_across_random_inputs_and_forms() {
    let forms = ["exists", "not_exists", "count", "sum", "in", "bare_scalar"];
    let mut trials = 0;
    for seed in 0..120u64 {
        let mut g = Gen::new(seed);
        let outer_n = g.rng.gen_range(0..=12);
        let inner_n = g.rng.gen_range(0..=12);
        let fixtures = make_fixtures(&mut g, outer_n, inner_n);

        for form in forms {
            let term = match form {
                "exists" => exists_term("lines", "l", false),
                "not_exists" => exists_term("lines", "l", true),
                "count" => agg_term("lines", "l", "count", "amt", "ge", col("want")),
                "sum" => agg_term("lines", "l", "sum", "amt", "ge", col("want")),
                "in" => in_term("lines", "l", "code", col("want"), false),
                "bare_scalar" => json!({
                    "kind": "scalar_sub",
                    "outer": col("want"),
                    "op": "eq",
                    "sub": sub("lines", "l",
                        json!([corr("o", "cust", "l", "cust")]),
                        json!({"project": qcol("l", "code")}))
                }),
                _ => unreachable!(),
            };

            // Crosscheck mode makes the pipeline itself assert equivalence;
            // additionally run each engine independently and compare.
            let build = |mode: &str, fx: &Value| {
                json!({
                    "query": {
                        "select": [{"column": "o_id"}],
                        "from": {"relation": "orders", "alias": "o"},
                        "where_terms": [term]
                    },
                    "fixtures": fx,
                    "options": {"mode": mode}
                })
            };

            let cross = run_body(build("crosscheck", &fixtures));
            let naive_res = run_body(build("naive", &fixtures));
            let rewrite_res = run_body(build("rewrite", &fixtures));

            assert_eq!(
                cross, naive_res,
                "crosscheck must agree with naive: seed {seed} form {form}"
            );
            match (&naive_res, &rewrite_res) {
                (Ok(a), Ok(b)) => assert_eq!(a, b, "row divergence seed {seed} form {form}"),
                (Err(ka), Err(kb)) => {
                    assert_eq!(ka, kb, "error kind divergence seed {seed} form {form}")
                }
                _ => panic!(
                    "ok/error divergence seed {seed} form {form}: {naive_res:?} vs {rewrite_res:?}"
                ),
            }
            trials += 1;
        }
    }
    assert!(trials >= 500, "expected many trials, got {trials}");
}

#[test]
fn batches_equal_detects_order_and_null_differences() {
    use decorrelate_svc::batch::{Batch, Column, DataType, Field, Scalar, Schema};
    use std::sync::Arc;

    fn batch(rows: &[(i64, Option<i64>)]) -> Batch {
        let schema = Arc::new(Schema::new(vec![
            Field::new("a", DataType::Int),
            Field::new("b", DataType::Int),
        ]));
        let mut a: Column = Vec::new();
        let mut b: Column = Vec::new();
        for (x, y) in rows {
            a.push(Scalar::Int(*x));
            b.push(y.map_or(Scalar::Null, Scalar::Int));
        }
        Batch::try_new(schema, vec![a, b]).unwrap()
    }

    let b1 = batch(&[(1, None), (2, Some(2))]);
    let b2 = batch(&[(1, None), (2, Some(2))]);
    assert!(batches_equal(&b1, &b2));

    let reordered = batch(&[(2, Some(2)), (1, None)]);
    assert!(!batches_equal(&b1, &reordered));

    let null_changed = batch(&[(1, Some(1)), (2, Some(2))]);
    assert!(!batches_equal(&b1, &null_changed));
}
