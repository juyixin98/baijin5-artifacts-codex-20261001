//! Randomized differential testing.
//!
//! For 200 deterministic pseudo-random scenarios (a tiny LCG — no extra
//! dependency) the decorrelated engine is compared against the independent
//! JSON oracle under random NULL placement, duplicate keys and (sometimes)
//! empty inner tables, across every supported op. Expected answers are never
//! produced by the engine itself.

mod common;

use common::{fixture_json, run_oracle, OracleOutcome, OracleQuery};
use decorr::ast::LoadFixturesRequest;
use decorr::{build_app_state, QueryService};
use serde_json::{json, Value};

/// Tiny deterministic LCG so runs are reproducible without a dependency.
struct Rng(u64);

impl Rng {
    fn next_u64(&mut self) -> u64 {
        self.0 = self
            .0
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        self.0
    }

    fn below(&mut self, bound: u64) -> u64 {
        self.next_u64() % bound
    }

    fn chance(&mut self, pct: u64) -> bool {
        self.below(100) < pct
    }

    /// Key from a small domain (forces collisions/duplicates), or NULL.
    fn key(&mut self, domain: i64, null_pct: u64) -> Option<i64> {
        if self.chance(null_pct) {
            None
        } else {
            Some(self.below(domain as u64) as i64)
        }
    }
}

fn make_relations(rng: &mut Rng) -> Value {
    let n_outer = rng.below(13) as usize; // 0..=12
                                          // Roughly 15% of scenarios get an empty inner table.
    let n_inner = if rng.chance(15) {
        0
    } else {
        rng.below(13) as usize
    };
    let domain = 4i64;

    let mut outer_rows = Vec::new();
    for o_id in 0..n_outer {
        outer_rows.push(json!({ "o_id": o_id as i64, "cust": rng.key(domain, 30) }));
    }

    let mut payment_rows = Vec::new();
    for p_id in 0..n_inner {
        payment_rows.push(json!({
            "p_id": p_id as i64,
            "cust": rng.key(domain, 25),
            "amount": if rng.chance(30) { Value::Null } else { Value::from(rng.below(20) as i64 - 10) },
        }));
    }

    // For scalar lookups: at most ONE non-NULL row per key so the cardinality
    // rule is not triggered (that path has dedicated counter-example tests).
    let mut label_rows = Vec::new();
    let mut used = std::collections::BTreeSet::new();
    for l_id in 0..n_inner {
        let _ = l_id;
        match rng.key(domain, 25) {
            Some(k) if used.insert(k) => {
                label_rows.push(json!({ "cust": k, "label": format!("L{k}") }));
            }
            _ => {}
        }
    }

    json!({
        "replace": true,
        "relations": {
            "orders": {
                "columns": [{ "name": "o_id", "type": "INTEGER" }, { "name": "cust", "type": "INTEGER" }],
                "rows": outer_rows
            },
            "payments": {
                "columns": [
                    { "name": "p_id", "type": "INTEGER" },
                    { "name": "cust", "type": "INTEGER" },
                    { "name": "amount", "type": "INTEGER" }
                ],
                "rows": payment_rows
            },
            "labels": {
                "columns": [
                    { "name": "cust", "type": "INTEGER" },
                    { "name": "label", "type": "TEXT" }
                ],
                "rows": label_rows
            }
        }
    })
}

fn assert_engine_matches_oracle(
    service: &QueryService,
    fixtures: &Value,
    q: &OracleQuery<'_>,
    seed: u64,
) {
    let body = common::query_request_json(q, &format!("diff-{seed}"));
    let req = serde_json::from_value(body).expect("query request deserializes");

    match (service.run_query(req), run_oracle(fixtures, q)) {
        (Ok(success), OracleOutcome::Rows(oracle_rows)) => {
            assert_eq!(
                success.rows.len(),
                oracle_rows.len(),
                "seed={seed} op={} row count",
                q.op
            );
            for (i, (got, want)) in success.rows.iter().zip(&oracle_rows).enumerate() {
                assert_eq!(
                    got.as_object().unwrap(),
                    want,
                    "seed={seed} op={} row {i}",
                    q.op
                );
            }
            if let Some(v) = success.equivalence.as_ref() {
                assert!(
                    v.equivalent,
                    "seed={seed} internal cross-check failed: {v:?}"
                );
            }
        }
        (Err(failure), OracleOutcome::Failure { category, .. }) => {
            assert_eq!(
                failure.failure.category, category,
                "seed={seed} op={}",
                q.op
            );
            assert_eq!(
                failure.failure_cross_check,
                Some(true),
                "seed={seed} op={}",
                q.op
            );
        }
        (got, want) => panic!(
            "seed={seed} op={} outcome mismatch: got={got:?} want={want:?}",
            q.op
        ),
    }
}

#[test]
fn randomized_differential_runs_match_independent_oracle() {
    let mut rng = Rng(0xDEAD_BEEF_CAFE_BABE);
    let mut exercised_empty_inner = false;

    for seed in 0..200u64 {
        let fixtures = make_relations(&mut rng);
        if fixtures["relations"]["payments"]["rows"]
            .as_array()
            .unwrap()
            .is_empty()
        {
            exercised_empty_inner = true;
        }

        // Load the random scenario. The main fixtures file is unrelated here,
        // so the oracle reads the same in-memory JSON it is given.
        let service = build_app_state();
        let req: LoadFixturesRequest =
            serde_json::from_value(fixtures.clone()).expect("random fixtures deserialize");
        service.load_fixtures(&req).expect("random fixtures load");

        let cases: Vec<OracleQuery<'_>> = vec![
            OracleQuery {
                outer_relation: "orders",
                inner_relation: "payments",
                select: &["o_id", "cust"],
                correlation: &[("cust", "cust")],
                op: "exists",
                aggregate: None,
                value_column: None,
                output_column: "v",
            },
            OracleQuery {
                op: "not_exists",
                output_column: "v",
                ..base_query()
            },
            OracleQuery {
                op: "scalar_aggregate",
                aggregate: Some("count_star"),
                output_column: "v",
                ..base_query()
            },
            OracleQuery {
                op: "scalar_aggregate",
                aggregate: Some("sum"),
                value_column: Some("amount"),
                output_column: "v",
                ..base_query()
            },
            OracleQuery {
                inner_relation: "labels",
                op: "scalar",
                value_column: Some("label"),
                output_column: "v",
                ..base_query()
            },
        ];

        for q in &cases {
            assert_engine_matches_oracle(&service, &fixtures, q, seed);
        }
    }

    assert!(
        exercised_empty_inner,
        "random run should include an empty inner case"
    );
    // Sanity: the fixed fixture file remains parseable for the oracle module.
    assert!(fixture_json()["relations"].is_object());
}

// All borrowed fields are &'static literals, so the base template carries a
// 'static lifetime; the helper keeps the case table readable.
fn base_query() -> OracleQuery<'static> {
    OracleQuery {
        outer_relation: "orders",
        inner_relation: "payments",
        select: &["o_id", "cust"],
        correlation: &[("cust", "cust")],
        op: "exists",
        aggregate: None,
        value_column: None,
        output_column: "v",
    }
}
