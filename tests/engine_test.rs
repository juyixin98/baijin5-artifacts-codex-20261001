//! Engine integration tests against the independent reference oracle.
//!
//! Every numeric result is asserted concretely (specific values, not just
//! "endpoint callable") and cross-checked against a naive in-memory SQL
//! implementation in `tests/common/reference.rs` that shares no engine code.

mod common;

use std::collections::BTreeMap;

use groupagg::batch::{Batch, Column, DataType, Field};
use groupagg::exec::cancel::Token;
use groupagg::exec::{Engine, EngineConfig};
use groupagg::fixtures;
use groupagg::plan::Plan;
use serde_json::json;
use tempfile::tempdir;

use common::reference::{engine_groups_as_json, reference_eval, RefGroup, RefValue};

fn test_engine(budget: usize, cancel_check_rows: u64) -> (Engine, tempfile::TempDir) {
    let dir = tempdir().expect("temp dir");
    let engine = Engine::new(EngineConfig {
        memory_budget_bytes: budget,
        spill_root: dir.path().to_path_buf(),
        cancel_check_rows,
    });
    (engine, dir)
}

fn plan(json: serde_json::Value) -> Plan {
    Plan::from_json(&json).expect("plan parses")
}

fn find_group<'a>(
    groups: &'a [(serde_json::Value, BTreeMap<String, serde_json::Value>)],
    key: &str,
) -> &'a BTreeMap<String, serde_json::Value> {
    groups
        .iter()
        .find(|(k, _)| k.get("g").and_then(|v| v.as_str()) == Some(key))
        .map(|(_, v)| v)
        .unwrap_or_else(|| panic!("group {key} not found"))
}

#[test]
fn hand_computed_even_sample_median_is_two_and_a_half() {
    let fixture = fixtures::even_sample();
    let (engine, _dir) = test_engine(512, 64); // tiny budget → multiple runs
    let p = plan(json!({
        "group_by": ["g"],
        "aggregations": [
            {"alias":"median","column":"score","op":"percentile_cont","quantile":0.5},
            {"alias":"p25","column":"score","op":"percentile_cont","quantile":0.25},
            {"alias":"dmedian","column":"level","op":"percentile_disc","quantile":0.5}
        ]
    }));
    let result = engine
        .execute(
            "even-1",
            &fixture.schema,
            &fixture.batches,
            &p,
            &Token::new(),
        )
        .expect("query succeeds");

    let groups = engine_groups_as_json(&result.groups, &p);
    let agg = find_group(&groups, "even");
    // rank = 0.5*(4-1) = 1.5 → 20 + 0.5*(30-20) = 25
    assert_eq!(agg["median"], json!(25.0));
    // rank = 0.25*3 = 0.75 → 10 + 0.75*(20-10) = 17.5
    assert_eq!(agg["p25"], json!(17.5));
    // discrete: ceil(0.5*4)=2 → 2nd ordered level = 2 (type preserved: int)
    assert_eq!(agg["dmedian"], json!(2));

    // External sort really spilled.
    assert!(
        result.stats.spill_runs >= 2,
        "tiny budget should force runs"
    );
    assert!(
        result.stats.peak_memory_bytes <= 512,
        "peak {} must stay within budget",
        result.stats.peak_memory_bytes
    );

    // Independent oracle agreement.
    let oracle = reference_eval(&fixture.schema, &fixture.batches, &p);
    assert_oracle_matches(&groups, oracle);
}

#[test]
fn all_null_group_returns_null_for_every_aggregate() {
    let fixture = fixtures::all_nulls();
    let (engine, _dir) = test_engine(1024, 16);
    let p = plan(json!({
        "group_by": ["g"],
        "aggregations": [
            {"alias":"c","column":"score","op":"percentile_cont","quantile":0.5},
            {"alias":"d","column":"level","op":"percentile_disc","quantile":0.9},
            {"alias":"m","column":"tag","op":"mode"},
            {"alias":"s","column":"tag","op":"string_agg","delimiter":","}
        ]
    }));
    let result = engine
        .execute(
            "null-1",
            &fixture.schema,
            &fixture.batches,
            &p,
            &Token::new(),
        )
        .expect("query succeeds");
    let groups = engine_groups_as_json(&result.groups, &p);
    let agg = find_group(&groups, "nullish");
    assert_eq!(agg["c"], json!(null));
    assert_eq!(agg["d"], json!(null));
    assert_eq!(agg["m"], json!(null));
    assert_eq!(agg["s"], json!(null));

    let oracle = reference_eval(&fixture.schema, &fixture.batches, &p);
    assert_oracle_matches(&groups, oracle);
}

#[test]
fn mode_tie_resolves_to_smallest_value() {
    let fixture = fixtures::mode_tie();
    let (engine, _dir) = test_engine(256, 8);
    let p = plan(json!({
        "group_by": ["g"],
        "aggregations": [
            {"alias":"m","column":"tag","op":"mode"}
        ]
    }));
    let result = engine
        .execute(
            "tie-1",
            &fixture.schema,
            &fixture.batches,
            &p,
            &Token::new(),
        )
        .unwrap();
    let groups = engine_groups_as_json(&result.groups, &p);
    assert_eq!(find_group(&groups, "tie")["m"], json!("a"));

    let oracle = reference_eval(&fixture.schema, &fixture.batches, &p);
    assert_oracle_matches(&groups, oracle);
}

#[test]
fn large_repeat_group_mode_and_ordered_aggregation() {
    let fixture = fixtures::large_repeat_group();
    let (engine, _dir) = test_engine(2048, 32);
    let p = plan(json!({
        "group_by": ["g"],
        "aggregations": [
            {"alias":"m","column":"tag","op":"mode"},
            {"alias":"asc","column":"tag","op":"string_agg","delimiter":","},
            {"alias":"desc","column":"tag","op":"string_agg","delimiter":"-","order":"desc"},
            {"alias":"p99","column":"score","op":"percentile_disc","quantile":0.99}
        ]
    }));
    let result = engine
        .execute(
            "big-1",
            &fixture.schema,
            &fixture.batches,
            &p,
            &Token::new(),
        )
        .unwrap();
    let groups = engine_groups_as_json(&result.groups, &p);
    let agg = find_group(&groups, "big");
    assert_eq!(agg["m"], json!("z"));

    let asc = agg["asc"].as_str().unwrap();
    let asc_parts: Vec<&str> = asc.split(',').collect();
    assert_eq!(asc_parts.len(), 903);
    // Three 'a' sort before the 900 z's.
    assert_eq!(&asc_parts[..3], &["a", "a", "a"]);
    assert!(asc_parts[3..].iter().all(|s| *s == "z"));

    let desc = agg["desc"].as_str().unwrap();
    let desc_parts: Vec<&str> = desc.split('-').collect();
    assert_eq!(desc_parts.len(), 903);
    assert!(desc_parts[..900].iter().all(|s| *s == "z"));
    assert_eq!(&desc_parts[900..], &["a", "a", "a"]);

    // 99th discrete percentile of 1..=903: ceil(0.99*903)=894 → value 894.0
    // (float64 column; PERCENTILE_DISC preserves the physical type).
    assert_eq!(agg["p99"], json!(894.0));

    let oracle = reference_eval(&fixture.schema, &fixture.batches, &p);
    assert_oracle_matches(&groups, oracle);
}

#[test]
fn nulls_are_filtered_but_rows_remain_counted_per_group() {
    // scores [1, null, 3, null] in one group → n=2; median = 2.0
    let schema = vec![
        Field::new("g", DataType::Utf8),
        Field::new("v", DataType::Float64),
    ];
    let b1 = Batch::new(
        schema.clone(),
        vec![
            Column::from_utf8(vec![Some("x".into()), Some("x".into())]),
            Column::from_f64(vec![Some(1.0), None]),
        ],
    )
    .unwrap();
    let b2 = Batch::new(
        schema.clone(),
        vec![
            Column::from_utf8(vec![Some("x".into()), Some("x".into())]),
            Column::from_f64(vec![Some(3.0), None]),
        ],
    )
    .unwrap();
    let (engine, _dir) = test_engine(128, 4);
    let p = plan(json!({
        "group_by": ["g"],
        "aggregations": [
            {"alias":"med","column":"v","op":"percentile_cont","quantile":0.5},
            {"alias":"d","column":"v","op":"percentile_disc","quantile":0.5}
        ]
    }));
    let result = engine
        .execute("n-1", &schema, &[b1, b2], &p, &Token::new())
        .unwrap();
    let groups = engine_groups_as_json(&result.groups, &p);
    let agg = find_group(&groups, "x");
    assert_eq!(agg["med"], json!(2.0));
    // PERCENTILE_DISC(0.5) with n=2: ceil(0.5*2)=1 → first ordered value.
    assert_eq!(agg["d"], json!(1.0));
}

#[test]
fn scalar_aggregation_without_group_by_and_zero_rows() {
    let schema = vec![Field::new("v", DataType::Int64)];
    let empty = Batch::new(schema.clone(), vec![Column::from_i64(Vec::new())]).unwrap();
    let (engine, _dir) = test_engine(1024, 16);
    let p = plan(json!({
        "aggregations": [
            {"alias":"d","column":"v","op":"percentile_disc","quantile":0.5},
            {"alias":"m","column":"v","op":"mode"}
        ]
    }));
    let result = engine
        .execute("scalar-empty", &schema, &[empty], &p, &Token::new())
        .unwrap();
    assert_eq!(result.groups.len(), 1);
    assert!(result.groups[0].key.is_empty());
    assert!(result.groups[0].values.iter().all(|v| v.is_none()));
}

#[test]
fn skewed_groups_keep_memory_bounded_and_results_correct() {
    let fixture = fixtures::skewed_groups(500, 100);
    let budget = 4096;
    let (engine, _dir) = test_engine(budget, 64);
    let p = plan(json!({
        "group_by": ["g"],
        "aggregations": [
            {"alias":"med","column":"score","op":"percentile_cont","quantile":0.5},
            {"alias":"m","column":"tag","op":"mode"},
            {"alias":"s","column":"tag","op":"string_agg","delimiter":"|"}
        ]
    }));
    let result = engine
        .execute(
            "skew-1",
            &fixture.schema,
            &fixture.batches,
            &p,
            &Token::new(),
        )
        .unwrap();
    assert_eq!(result.stats.groups_emitted, 101); // 1 heavy + 100 singleton
    assert!(result.stats.spill_runs >= 5, "skew must force many runs");
    assert!(result.stats.peak_memory_bytes <= budget);

    let groups = engine_groups_as_json(&result.groups, &p);
    let agg = find_group(&groups, "heavy");
    // scores 0..500 median = 249.5
    assert_eq!(agg["med"], json!(249.5));

    let oracle = reference_eval(&fixture.schema, &fixture.batches, &p);
    assert_oracle_matches(&groups, oracle);
}

#[test]
fn cancellation_during_merge_then_resume_gives_identical_result() {
    let fixture = fixtures::large_repeat_group();
    let (engine, _dir) = test_engine(2048, 1); // check after every merged record
    let p = plan(json!({
        "group_by": ["g"],
        "aggregations": [
            {"alias":"m","column":"tag","op":"mode"},
            {"alias":"p50","column":"score","op":"percentile_cont","quantile":0.5},
            {"alias":"s","column":"tag","op":"string_agg","delimiter":","}
        ]
    }));

    // Phase 1: durable ingest.
    let token = engine
        .prepare(
            "cancel-1",
            &fixture.schema,
            &fixture.batches,
            &p,
            &Token::new(),
        )
        .unwrap();

    // Phase 2 attempt: cancel almost immediately into the first counting pass.
    let doomed = Token::new();
    doomed.cancel();
    let err = engine.resume(&token, &p, &doomed).unwrap_err();
    assert_eq!(err.kind, groupagg::ErrorKind::Cancelled);

    // Spill state is intact and manifest says complete/resumable.
    let manifest = engine.manifest_stats("cancel-1").expect("manifest exists");
    assert!(manifest.complete);
    assert!(manifest.runs.len() >= 2);
    assert_eq!(manifest.ingested_rows, 903);

    // Resume with a fresh token → identical answer to an uninterrupted run.
    let resumed = engine.resume(&token, &p, &Token::new()).unwrap();
    let resumed_json = engine_groups_as_json(&resumed.groups, &p);
    let agg = find_group(&resumed_json, "big");
    assert_eq!(agg["m"], json!("z"));
    assert_eq!(agg["p50"], json!(452.0)); // median of 1..=903
    assert_eq!(agg["s"].as_str().unwrap().split(',').count(), 903);

    let oracle = reference_eval(&fixture.schema, &fixture.batches, &p);
    assert_oracle_matches(&resumed_json, oracle);
}

#[test]
fn resume_rejects_token_replayed_with_different_plan() {
    let fixture = fixtures::mode_tie();
    let (engine, _dir) = test_engine(1024, 16);
    let p1 = plan(json!({
        "group_by": ["g"],
        "aggregations": [{"alias":"m","column":"tag","op":"mode"}]
    }));
    let token = engine
        .prepare(
            "resume-mismatch",
            &fixture.schema,
            &fixture.batches,
            &p1,
            &Token::new(),
        )
        .unwrap();

    let p2 = plan(json!({
        "group_by": ["g"],
        "aggregations": [
            {"alias":"m","column":"score","op":"percentile_cont","quantile":0.5}
        ]
    }));
    let err = engine.resume(&token, &p2, &Token::new()).unwrap_err();
    assert_eq!(err.kind, groupagg::ErrorKind::InvalidResume);
}

#[test]
fn quantile_out_of_range_is_rejected_before_ingest() {
    let (_engine, dir) = test_engine(1024, 16);
    let p = Plan::from_json(&json!({
        "group_by": ["g"],
        "aggregations": [
            {"alias":"bad","column":"score","op":"percentile_cont","quantile":1.0001}
        ]
    }))
    .unwrap_err();
    assert_eq!(p.kind, groupagg::ErrorKind::InvalidQuantile);

    // Engine-level guard also rejects even a hand-built plan.
    let bad = Plan::from_json(&json!({
        "aggregations": [
            {"alias":"bad","column":"score","op":"percentile_disc","quantile":-0.2}
        ]
    }))
    .unwrap_err();
    assert_eq!(bad.kind, groupagg::ErrorKind::InvalidQuantile);

    // Nothing spilled: validation never touched the spill root.
    assert!(dir.path().read_dir().unwrap().next().is_none());
}

#[test]
fn randomized_inputs_agree_with_independent_oracle() {
    // Deterministic LCG — no external RNG dependency.
    let mut state = 0x1234_5678u64;
    let mut next = || {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        state
    };

    let schema = vec![
        Field::new("g", DataType::Utf8),
        Field::new("score", DataType::Float64),
        Field::new("level", DataType::Int64),
        Field::new("tag", DataType::Utf8),
    ];

    type Row = (String, Option<f64>, Option<i64>, Option<String>);

    for round in 0..8 {
        let row_count = 50 + (next() % 400) as usize;
        let group_count = 1 + (next() % 6) as usize;
        let mut all_rows: Vec<Row> = Vec::with_capacity(row_count);
        for _ in 0..row_count {
            let g = format!("grp-{}", next() as usize % group_count);
            let score = if next() % 5 == 0 {
                None
            } else {
                Some((next() % 200) as f64 * 0.5 - 40.0)
            };
            let level = if next() % 7 == 0 {
                None
            } else {
                Some((next() % 50) as i64 - 10)
            };
            let tag = if next() % 4 == 0 {
                None
            } else {
                Some(format!("t{}", next() % 5)) // small domain → real mode ties
            };
            all_rows.push((g, score, level, tag));
        }

        // Split rows across 1-4 batches.
        let splits = 1 + (next() % 4) as usize;
        let mut batches = Vec::new();
        let chunks: Vec<Vec<_>> = (0..splits)
            .map(|s| {
                let lo = s * row_count / splits;
                let hi = (s + 1) * row_count / splits;
                all_rows[lo..hi].to_vec()
            })
            .collect();
        for chunk in chunks {
            if chunk.is_empty() {
                continue;
            }
            batches.push(
                Batch::new(
                    schema.clone(),
                    vec![
                        Column::from_utf8(chunk.iter().map(|r| Some(r.0.clone())).collect()),
                        Column::from_f64(chunk.iter().map(|r| r.1).collect()),
                        Column::from_i64(chunk.iter().map(|r| r.2).collect()),
                        Column::from_utf8(chunk.iter().map(|r| r.3.clone()).collect()),
                    ],
                )
                .unwrap(),
            );
        }
        if batches.is_empty() {
            continue;
        }

        let quantile = (next() % 101) as f64 / 100.0; // exact 0.0..=1.0 values
        let descending = next() % 2 == 0;
        let p = plan(json!({
            "group_by": ["g"],
            "aggregations": [
                {"alias":"c","column":"score","op":"percentile_cont","quantile": quantile},
                {"alias":"d","column":"level","op":"percentile_disc","quantile": quantile},
                {"alias":"m","column":"tag","op":"mode"},
                {"alias":"s","column":"tag","op":"string_agg","delimiter":",",
                 "order": if descending { "desc" } else { "asc" }}
            ]
        }));

        let (engine, _dir) = test_engine(300 + (next() % 4000) as usize, 8);
        let result = engine
            .execute(
                &format!("random-{round}"),
                &schema,
                &batches,
                &p,
                &Token::new(),
            )
            .expect("randomized query succeeds");
        let groups = engine_groups_as_json(&result.groups, &p);
        let oracle = reference_eval(&schema, &batches, &p);
        assert_oracle_matches(&groups, oracle);
    }
}

fn assert_oracle_matches(
    engine_groups: &[(serde_json::Value, BTreeMap<String, serde_json::Value>)],
    oracle: Vec<RefGroup>,
) {
    // Convert oracle keys to the same JSON shape (`g` column).
    let mut oracle_map = BTreeMap::new();
    for g in &oracle {
        let key = match g.key.first() {
            None => json!({}),
            Some(RefValue::Text(s)) => json!({"g": s}),
            Some(RefValue::Int(i)) => json!({"g": i}),
            Some(other) => panic!("unexpected key type {other:?}"),
        };
        let vals: BTreeMap<String, serde_json::Value> = g
            .values
            .iter()
            .map(|(k, v)| (k.clone(), v.clone().unwrap_or(json!(null))))
            .collect();
        oracle_map.insert(key.to_string(), vals);
    }
    assert_eq!(engine_groups.len(), oracle.len(), "group count differs");
    for (key, vals) in engine_groups {
        let expected = oracle_map
            .get(&key.to_string())
            .unwrap_or_else(|| panic!("oracle has no group {key}"));
        for (alias, value) in vals {
            assert_eq!(value, &expected[alias], "mismatch for {alias} in {key}");
        }
    }
}
