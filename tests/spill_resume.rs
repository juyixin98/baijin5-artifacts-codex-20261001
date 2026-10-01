//! External-sort spilling, cooperative cancellation and resume.
//!
//! A tiny per-query memory budget forces many runs; the local fault-injection
//! hint cancels after the Nth durable run, then the returned resume token
//! completes the identical request. Final values must match the reference
//! evaluator exactly.

mod common;

use common::{find_group, reference, result_value, test_config};
use pctl::config::Config;
use pctl::diagnostics::RequestId;
use pctl::exec::{execute_request, ExecOutcome};
use pctl::resources::CancellationToken;
use pctl::spec::{QueryRequest, ResumeToken};
use serde_json::{json, Value};

fn spill_config() -> (Config, tempfile::TempDir) {
    let (mut cfg, td) = test_config();
    // Tiny budget: every entry charges >= 56 bytes (+ string payload), so a few
    // hundred rows guarantee repeated spilling and k-way merges.
    cfg.memory_budget_bytes = 64 * 1024;
    (cfg, td)
}

fn parse(v: Value) -> QueryRequest {
    serde_json::from_value(v).unwrap()
}

/// Large-ish grouped dataset with skew toward group "hot".
fn dataset(rows: u64) -> Value {
    let mut groups = Vec::new();
    let mut measure = Vec::new();
    let mut words = Vec::new();
    let mut lcg = common::Lcg(0x1234_5678_9abc_def0);
    for i in 0..rows {
        // 70% "hot", 20% "warm", 10% spread over five cold groups -> skew
        let bucket = lcg.below(10);
        let g = if bucket < 7 {
            "hot"
        } else if bucket < 9 {
            "warm"
        } else {
            match i % 5 {
                0 => "c0",
                1 => "c1",
                2 => "c2",
                3 => "c3",
                _ => "c4",
            }
        };
        groups.push(json!(g));
        measure.push(json!((lcg.below(1000) as i64) - 500));
        let w = match lcg.below(6) {
            0 => "alpha",
            1 => "beta",
            2 => "gamma",
            3 => "delta",
            4 => "epsilon",
            _ => "zeta",
        };
        words.push(json!(w));
        let _ = i;
    }
    json!({
        "group_by": "g",
        "columns": [
            { "name": "g", "data_type": "utf8", "values": groups },
            { "name": "v", "data_type": "i64", "values": measure },
            { "name": "s", "data_type": "utf8", "values": words }
        ],
        "operators": [
            { "op": "percentile", "column": "v", "p": 0.25, "method": "continuous" },
            { "op": "percentile", "column": "v", "p": 0.75, "method": "discrete" },
            { "op": "percentile", "column": "s", "p": 0.5, "method": "discrete" },
            { "op": "mode", "column": "v" },
            { "op": "mode", "column": "s" },
            { "op": "string_agg", "column": "s", "delimiter": "|", "order": "asc" },
            { "op": "string_agg", "column": "s", "delimiter": "|", "order": "desc" }
        ]
    })
}

#[test]
fn forced_spilling_matches_reference() {
    let (cfg, _td) = spill_config();
    let req = dataset(2_000);
    let parsed = parse(req.clone());
    let outcome =
        execute_request(&parsed, &RequestId::new(), &cfg, CancellationToken::new()).unwrap();
    let groups = match outcome {
        ExecOutcome::Complete(g, stats) => {
            assert!(stats.runs_spilled > 0, "tiny budget must force spills");
            assert!(stats.bytes_spilled > 0);
            g
        }
        other => panic!("expected complete, got {other:?}"),
    };
    let resp = json!({ "status": "complete",
        "groups": groups.into_iter().map(|g| serde_json::to_value(g).unwrap()).collect::<Vec<_>>() });

    // Every group/op must match the independent in-memory reference.
    let expected = reference::evaluate(&req);
    for (_k, want) in expected {
        let got = find_group(&resp, want["group"].clone());
        for (op, w) in want["results"].as_array().unwrap().iter().enumerate() {
            assert_eq!(
                result_value(got, op),
                w["value"],
                "group={} op={op}",
                want["group"]
            );
            if w.get("frequency").is_some() {
                assert_eq!(got["results"][op]["frequency"], w["frequency"]);
                assert_eq!(got["results"][op]["tie"], w["tie"]);
            }
        }
    }
}

#[test]
fn cancel_after_run_then_resume_completes() {
    let (cfg, _td) = spill_config();
    let req = dataset(1_500);

    // First attempt: inject cancellation immediately after the first run.
    let mut first = req.clone();
    first["hints"] = json!({ "cancel_after_runs": 1, "memory_budget_bytes": 65536 });
    let parsed = parse(first);
    let outcome =
        execute_request(&parsed, &RequestId::new(), &cfg, CancellationToken::new()).unwrap();
    let (token, cursor) = match outcome {
        ExecOutcome::Cancelled {
            resume,
            cursor,
            stats,
        } => {
            assert!(stats.runs_spilled >= 1);
            assert!(cursor > 0, "checkpoint cursor counts fully-ingested rows");
            (resume, cursor)
        }
        other => panic!("expected cancellation, got {other:?}"),
    };
    assert!(cursor < 1_500, "cancelled before all rows ingested");

    // Resume with the same payload + token. Do not re-arm injection.
    let mut second = req.clone();
    second["hints"] = json!({
        "memory_budget_bytes": 65536,
        "resume": { "spill_dir": token.spill_dir, "ordinal_cursor": token.ordinal_cursor }
    });
    let parsed2 = parse(second);
    let outcome2 =
        execute_request(&parsed2, &RequestId::new(), &cfg, CancellationToken::new()).unwrap();
    let groups = match outcome2 {
        ExecOutcome::Complete(g, _) => g,
        other => panic!("resume must complete, got {other:?}"),
    };

    let resp = json!({ "status": "complete",
        "groups": groups.into_iter().map(|g| serde_json::to_value(g).unwrap()).collect::<Vec<_>>() });
    let expected = reference::evaluate(&req);
    for (_k, want) in expected {
        let got = find_group(&resp, want["group"].clone());
        assert_eq!(
            got["rows"], want["rows"],
            "resume double-count? group={}",
            want["group"]
        );
        for (op, w) in want["results"].as_array().unwrap().iter().enumerate() {
            assert_eq!(
                result_value(got, op),
                w["value"],
                "post-resume mismatch group={} op={op}",
                want["group"]
            );
        }
    }
}

#[test]
fn resume_rejects_wrong_plan() {
    let (cfg, _td) = spill_config();
    let req = dataset(400);
    let mut first = req.clone();
    first["hints"] = json!({ "cancel_after_runs": 1, "memory_budget_bytes": 65536 });
    let outcome = execute_request(
        &parse(first),
        &RequestId::new(),
        &cfg,
        CancellationToken::new(),
    )
    .unwrap();
    let token: ResumeToken = match outcome {
        ExecOutcome::Cancelled { resume, .. } => resume,
        other => panic!("{other:?}"),
    };

    // Replay with a changed quantile -> fingerprint mismatch -> validation reject.
    let mut tampered = req.clone();
    tampered["operators"][0]["p"] = json!(0.99);
    tampered["hints"] = json!({
        "memory_budget_bytes": 65536,
        "resume": { "spill_dir": token.spill_dir, "ordinal_cursor": token.ordinal_cursor }
    });
    let err = execute_request(
        &parse(tampered),
        &RequestId::new(),
        &cfg,
        CancellationToken::new(),
    )
    .expect_err("plan mismatch must be rejected");
    assert_eq!(err.kind, pctl::error::ErrorKind::Validation);
    assert_eq!(err.code, "resume_plan_mismatch");
}

#[test]
fn group_table_cap_is_enforced() {
    let (mut cfg, _td) = test_config();
    cfg.group_table_cap = 3;
    // 5 distinct groups, small data
    let req = json!({
        "group_by": "g",
        "columns": [
            { "name": "g", "data_type": "i64", "values": [0,1,2,3,4] },
            { "name": "v", "data_type": "i64", "values": [1,1,1,1,1] }
        ],
        "operators": [ { "op": "mode", "column": "v" } ]
    });
    let err = execute_request(
        &parse(req),
        &RequestId::new(),
        &cfg,
        CancellationToken::new(),
    )
    .expect_err("cap must be enforced");
    assert_eq!(err.kind, pctl::error::ErrorKind::Resource);
    assert_eq!(err.code, "groups_cap_exceeded");
}
