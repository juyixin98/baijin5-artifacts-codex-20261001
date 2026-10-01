//! Correctness + failure-category integration tests for the six operators.
//!
//! The expected answers come from `common` (an independent multiset oracle
//! that parses fixtures with its own parser). Production output is rendered to
//! JSON and compared as a multiset, in every execution mode.

mod common;

use common::{
    RefScalar, Rng, add_row, assert_multisets_equal, json_rows_to_multiset, parse_fixture,
    reference,
};
use set_ops::batch::json::batch_to_json;
use set_ops::batch::{Schema, read_typed_csv_batches};
use set_ops::error::{ErrorKind, InputCode, ResourceCode};
use set_ops::operator::{ExecutionMode, Qualifier, Query, SetOp, execute};
use set_ops::resource::ResourceLimits;
use std::collections::HashMap;
use std::path::PathBuf;
use tempfile::tempdir;

fn project_fixture(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("fixtures")
        .join(name)
}

fn output_json(out: &set_ops::operator::SetOpOutput) -> Vec<Vec<serde_json::Value>> {
    out.batches.iter().flat_map(batch_to_json).collect()
}

fn run_fixture_pair(
    left: &str,
    right: &str,
    op: SetOp,
    q: Qualifier,
    mode: ExecutionMode,
    limits: ResourceLimits,
    spill: Option<&std::path::Path>,
) -> set_ops::Result<set_ops::operator::SetOpOutput> {
    let l = read_typed_csv_batches(project_fixture(left), 13).unwrap();
    let r = read_typed_csv_batches(project_fixture(right), 17).unwrap();
    execute(Query::new(op, q, l, r), limits, mode, spill, None)
}

fn scalar_int(v: i64) -> RefScalar {
    RefScalar::Int(v)
}
fn scalar_text(v: &str) -> RefScalar {
    RefScalar::Text(v.to_string())
}

/// Hand-specified scenario with exact multiplicities.
fn explicit_multisets() -> (common::Multiset, common::Multiset) {
    // schema: id:bigint, label:text
    let mut l = HashMap::new();
    let mut r = HashMap::new();
    let row = |id: i64, label: Option<&str>| {
        let mut v = vec![scalar_int(id)];
        v.push(match label {
            Some(s) => scalar_text(s),
            None => RefScalar::Null,
        });
        v
    };
    add_row(&mut l, row(1, Some("a")), 2);
    add_row(&mut l, row(2, None), 1);
    add_row(&mut l, row(3, Some("12")), 1);
    add_row(&mut l, row(4, Some("1,23")), 1);
    add_row(&mut r, row(1, Some("a")), 3);
    add_row(&mut r, row(2, None), 1);
    add_row(&mut r, row(3, None), 1);
    add_row(&mut r, row(5, Some("12")), 1);
    (l, r)
}

fn build_explicit_batches(ms: &common::Multiset) -> Vec<set_ops::batch::TypedBatch> {
    let schema = Schema::parse_header("id:bigint,label:text").unwrap();
    let mut json_rows = Vec::new();
    for (row, mult) in ms {
        for _ in 0..*mult {
            json_rows.push(
                row.iter()
                    .map(|s| match s {
                        RefScalar::Null => serde_json::Value::Null,
                        RefScalar::Int(i) => serde_json::json!(i),
                        RefScalar::Text(t) => serde_json::json!(t),
                        other => panic!("unexpected {other:?}"),
                    })
                    .collect(),
            );
        }
    }
    // Deliberately split into small batches.
    json_rows
        .chunks(2)
        .map(|c| set_ops::batch::build_batch(&schema, c).unwrap())
        .collect()
}

#[test]
fn all_six_operators_match_reference_explicitly() {
    let (lms, rms) = explicit_multisets();
    let cases = [
        (SetOp::Union, "UNION", Qualifier::All, "ALL"),
        (SetOp::Union, "UNION", Qualifier::Distinct, "DISTINCT"),
        (SetOp::Intersect, "INTERSECT", Qualifier::All, "ALL"),
        (
            SetOp::Intersect,
            "INTERSECT",
            Qualifier::Distinct,
            "DISTINCT",
        ),
        (SetOp::Except, "EXCEPT", Qualifier::All, "ALL"),
        (SetOp::Except, "EXCEPT", Qualifier::Distinct, "DISTINCT"),
    ];
    for (op, opn, q, qn) in cases {
        let want = reference(opn, qn, &lms, &rms);
        let l = build_explicit_batches(&lms);
        let r = build_explicit_batches(&rms);
        let out = execute(
            Query::new(op, q, l, r),
            ResourceLimits::default(),
            ExecutionMode::InMemory,
            None,
            None,
        )
        .unwrap();
        let got = json_rows_to_multiset(&output_json(&out), &["bigint".into(), "text".into()]);
        assert_multisets_equal(&got, &want);

        // Concrete result assertions, not just "it ran".
        let rows = output_json(&out);
        match (opn, qn) {
            ("UNION", "ALL") => {
                assert_eq!(out.stats.output_rows, 11, "L=5 R=6 UNION ALL");
            }
            ("UNION", "DISTINCT") => assert_eq!(out.stats.output_rows, 6),
            ("INTERSECT", "ALL") => {
                assert_eq!(out.stats.output_rows, 3, "(1,a)x2 + (2,NULL)x1");
                let null_label_rows = rows
                    .iter()
                    .filter(|r| r[0] == serde_json::json!(2) && r[1].is_null())
                    .count();
                assert_eq!(null_label_rows, 1);
            }
            ("INTERSECT", "DISTINCT") => assert_eq!(out.stats.output_rows, 2),
            ("EXCEPT", "ALL") => {
                assert_eq!(out.stats.output_rows, 2, "(3,12) and (4,\"1,23\")");
                assert_eq!(out.stats.output_distinct, 2);
            }
            ("EXCEPT", "DISTINCT") => assert_eq!(out.stats.output_rows, 2),
            _ => unreachable!(),
        }
    }
}

#[test]
fn null_equality_and_nested_null_positions_are_fixed() {
    // edge fixtures: (x,NULL),(NULL,y),(NULL,NULL),(12,3),(1,23),("1","23")
    // right repeats (NULL,NULL) twice and has (12,3),(1,23) too.
    let out = run_fixture_pair(
        "edge_left.csv",
        "edge_right.csv",
        SetOp::Intersect,
        Qualifier::All,
        ExecutionMode::InMemory,
        ResourceLimits::default(),
        None,
    )
    .unwrap();
    let got = json_rows_to_multiset(&output_json(&out), &["text".into(), "text".into()]);
    let mut want = HashMap::new();
    add_row(&mut want, vec![scalar_text("x"), RefScalar::Null], 1);
    add_row(&mut want, vec![RefScalar::Null, scalar_text("y")], 1);
    add_row(&mut want, vec![RefScalar::Null, RefScalar::Null], 1); // min(1,2)
    add_row(&mut want, vec![scalar_text("12"), scalar_text("3")], 1);
    add_row(&mut want, vec![scalar_text("1"), scalar_text("23")], 1);
    assert_multisets_equal(&got, &want);

    // (NULL,NULL) is ONE distinct row, not a cartesian-ish collapse: EXCEPT ALL
    // of right(2) - left(1) for that key is 1 copy on the right side only.
    let out2 = run_fixture_pair(
        "edge_right.csv",
        "edge_left.csv",
        SetOp::Except,
        Qualifier::All,
        ExecutionMode::InMemory,
        ResourceLimits::default(),
        None,
    )
    .unwrap();
    let got2 = json_rows_to_multiset(&output_json(&out2), &["text".into(), "text".into()]);
    let mut want2 = HashMap::new();
    add_row(&mut want2, vec![RefScalar::Null, RefScalar::Null], 1);
    assert_multisets_equal(&got2, &want2);
}

// ---- external memory -------------------------------------------------------

fn write_stress_pair(dir: &std::path::Path, rows: usize, keyspace: usize) -> (PathBuf, PathBuf) {
    // Local generator (same xorshift family as the reference tests, not the
    // production generator): duplicates, skew, collision-prone strings.
    let header = "id:bigint,grp:bigint,label:text,flag:boolean,score:double\n";
    let labels = [
        "123", "1,23", "12,3", "1", "22", "\"q\"", "héllo", "", "\\N",
    ];
    let mut paths = (PathBuf::new(), PathBuf::new());
    for (side, seed) in [("left.csv", 0xA5A5_1234_u64), ("right.csv", 0xDEAD_9999)].iter() {
        let mut rng = Rng(*seed | 1);
        let path = dir.join(side);
        let mut f = std::fs::File::create(&path).unwrap();
        use std::io::Write;
        f.write_all(header.as_bytes()).unwrap();
        for i in 0..rows {
            let roll = rng.next_u64() % 100;
            let grp = if roll < 30 {
                0
            } else {
                (rng.next_u64() as usize) % keyspace
            };
            let label = labels[(rng.next_u64() as usize) % labels.len()];
            let flag = if i % 11 == 0 {
                ""
            } else if grp % 2 == 0 {
                "true"
            } else {
                "false"
            };
            let score = if i % 13 == 0 {
                String::new()
            } else {
                format!("{:.2}", grp as f64 / 3.0)
            };
            let field = if label.contains([',', '"']) {
                format!("\"{}\"", label.replace('"', "\"\""))
            } else {
                label.to_string()
            };
            writeln!(f, "{i},{grp},{field},{flag},{score}").unwrap();
        }
        if side == &"left.csv" {
            paths.0 = path;
        } else {
            paths.1 = path;
        }
    }
    paths
}

fn tight_limits(memory_bytes: usize, max_count: u64) -> ResourceLimits {
    ResourceLimits {
        memory_bytes,
        allow_spill: true,
        partition_fanout: 4,
        max_partition_depth: 6,
        max_count,
        ..ResourceLimits::default()
    }
}

#[test]
fn external_mode_matches_reference_and_really_spills() {
    let dir = tempdir().unwrap();
    let spill = tempdir().unwrap();
    let (lp, rp) = write_stress_pair(dir.path(), 4000, 40);
    let (types, lms) = parse_fixture(&lp);
    let (_, rms) = parse_fixture(&rp);

    for (op, opn, q, qn) in [
        (SetOp::Union, "UNION", Qualifier::All, "ALL"),
        (SetOp::Union, "UNION", Qualifier::Distinct, "DISTINCT"),
        (SetOp::Intersect, "INTERSECT", Qualifier::All, "ALL"),
        (
            SetOp::Intersect,
            "INTERSECT",
            Qualifier::Distinct,
            "DISTINCT",
        ),
        (SetOp::Except, "EXCEPT", Qualifier::All, "ALL"),
        (SetOp::Except, "EXCEPT", Qualifier::Distinct, "DISTINCT"),
    ] {
        let want = reference(opn, qn, &lms, &rms);
        let limits = tight_limits(300, u64::MAX);
        let out = run_pair_files(
            &lp,
            &rp,
            op,
            q,
            ExecutionMode::Auto,
            limits,
            Some(spill.path()),
        )
        .unwrap();
        let got = json_rows_to_multiset(&output_json(&out), &types);
        assert_multisets_equal(&got, &want);
        assert!(
            out.stats.spills > 0,
            "{opn} {qn}: expected real spills under 300-byte budget"
        );
        assert!(
            out.stats.max_resident_bytes <= 300 + 128,
            "{opn} {qn}: resident {} exceeded budget+epsilon",
            out.stats.max_resident_bytes
        );

        // Forced-external path must agree too and persist frames for every side.
        let out2 = run_pair_files(
            &lp,
            &rp,
            op,
            q,
            ExecutionMode::External,
            tight_limits(64 * 1024 * 1024, u64::MAX),
            Some(spill.path()),
        )
        .unwrap();
        let got2 = json_rows_to_multiset(&output_json(&out2), &types);
        assert_multisets_equal(&got2, &want);
        assert!(out2.stats.spills >= 2, "external mode spills both sides");
    }
}

fn run_pair_files(
    lp: &std::path::Path,
    rp: &std::path::Path,
    op: SetOp,
    q: Qualifier,
    mode: ExecutionMode,
    limits: ResourceLimits,
    spill: Option<&std::path::Path>,
) -> set_ops::Result<set_ops::operator::SetOpOutput> {
    let l = read_typed_csv_batches(lp, 31).unwrap();
    let r = read_typed_csv_batches(rp, 29).unwrap();
    execute(Query::new(op, q, l, r), limits, mode, spill, None)
}

#[test]
fn memory_and_external_modes_agree_on_tiny_skewed_data() {
    let dir = tempdir().unwrap();
    let spill = tempdir().unwrap();
    let (lp, rp) = write_stress_pair(dir.path(), 800, 8); // heavy skew + dups
    let (types, lms) = parse_fixture(&lp);
    let (_, rms) = parse_fixture(&rp);
    for (op, opn, q, qn) in [
        (SetOp::Union, "UNION", Qualifier::All, "ALL"),
        (SetOp::Intersect, "INTERSECT", Qualifier::All, "ALL"),
        (SetOp::Except, "EXCEPT", Qualifier::All, "ALL"),
    ] {
        let want = reference(opn, qn, &lms, &rms);
        let mem = run_pair_files(
            &lp,
            &rp,
            op,
            q,
            ExecutionMode::InMemory,
            ResourceLimits::default(),
            None,
        )
        .unwrap();
        let ext = run_pair_files(
            &lp,
            &rp,
            op,
            q,
            ExecutionMode::Auto,
            tight_limits(200, u64::MAX),
            Some(spill.path()),
        )
        .unwrap();
        let a = json_rows_to_multiset(&output_json(&mem), &types);
        let b = json_rows_to_multiset(&output_json(&ext), &types);
        assert_multisets_equal(&a, &want);
        assert_multisets_equal(&b, &want);
    }
}

// ---- hash collisions require true row comparison --------------------------

#[test]
fn colliding_buckets_keep_distinct_rows_apart() {
    use set_ops::batch::encode::{canonical_hash, level_partition};
    // Find a set of distinct text pairs that share the level-0 bucket under
    // fanout 2, then prove UNION DISTINCT keeps all of them distinct.
    let schema = Schema::parse_header("a:text").unwrap();
    let mut found: Vec<String> = Vec::new();
    let mut i: u64 = 0;
    while found.len() < 6 {
        let s = format!("collide-{i}");
        let h = canonical_hash(s.as_bytes());
        if level_partition(h, 0, 2) == 0 {
            found.push(s);
        }
        i += 1;
    }
    assert!(found.len() == 6, "brute force finds bucket-mates");
    let rows: Vec<Vec<serde_json::Value>> =
        found.iter().map(|s| vec![serde_json::json!(s)]).collect();
    let batch = set_ops::batch::build_batch(&schema, &rows).unwrap();
    let out = execute(
        Query::new(SetOp::Union, Qualifier::Distinct, vec![batch], vec![]),
        tight_limits(64, u64::MAX),
        ExecutionMode::Auto,
        Some(tempdir().unwrap().path()),
        None,
    )
    .unwrap();
    assert_eq!(out.stats.output_distinct, 6);
    assert_eq!(out.stats.output_rows, 6);
}

// ---- failure categories ----------------------------------------------------

fn one_col_batch(values: &[serde_json::Value]) -> Vec<set_ops::batch::TypedBatch> {
    let schema = Schema::parse_header("v:bigint").unwrap();
    let rows: Vec<Vec<serde_json::Value>> = values.iter().map(|v| vec![v.clone()]).collect();
    vec![set_ops::batch::build_batch(&schema, &rows).unwrap()]
}

#[test]
fn count_overflow_is_rejected_not_wrapped() {
    let left = one_col_batch(&std::iter::repeat_n(serde_json::json!(1), 4).collect::<Vec<_>>());
    let right = vec![];
    let limits = ResourceLimits {
        max_count: 3,
        ..tight_limits(1 << 20, 3)
    };
    let err = execute(
        Query::new(SetOp::Union, Qualifier::All, left, right),
        limits,
        ExecutionMode::InMemory,
        None,
        None,
    )
    .unwrap_err();
    assert!(
        matches!(
            err.kind,
            ErrorKind::ResourceExhausted(ResourceCode::CountOverflow)
        ),
        "got {err:?}"
    );
    // Error verdict must be replayable in the log-less run too (message set).
    assert!(err.message.contains("multiplicity"));
}

#[test]
fn union_all_overflow_across_sides_is_rejected() {
    let left = one_col_batch(&std::iter::repeat_n(serde_json::json!(1), 2).collect::<Vec<_>>());
    let right = one_col_batch(&std::iter::repeat_n(serde_json::json!(1), 2).collect::<Vec<_>>());
    let limits = ResourceLimits {
        max_count: 3,
        ..tight_limits(1 << 20, 3)
    };
    let err = execute(
        Query::new(SetOp::Union, Qualifier::All, left, right),
        limits,
        ExecutionMode::InMemory,
        None,
        None,
    )
    .unwrap_err();
    assert!(matches!(
        err.kind,
        ErrorKind::ResourceExhausted(ResourceCode::CountOverflow)
    ));
}

#[test]
fn in_memory_budget_breach_is_rejected_with_memory_budget_kind() {
    let schema = Schema::parse_header("v:text").unwrap();
    let rows: Vec<Vec<serde_json::Value>> = (0..50)
        .map(|i| vec![serde_json::Value::String(format!("row-{i:04}"))])
        .collect();
    let batch = set_ops::batch::build_batch(&schema, &rows).unwrap();
    let limits = ResourceLimits::no_spill(64);
    let err = execute(
        Query::new(SetOp::Union, Qualifier::Distinct, vec![batch], vec![]),
        limits,
        ExecutionMode::InMemory,
        None,
        None,
    )
    .unwrap_err();
    assert!(matches!(
        err.kind,
        ErrorKind::ResourceExhausted(ResourceCode::MemoryBudget)
    ));
}

#[test]
fn partition_depth_breach_is_its_own_failure_kind() {
    let schema = Schema::parse_header("v:text").unwrap();
    let rows: Vec<Vec<serde_json::Value>> = (0..20)
        .map(|i| vec![serde_json::Value::String(format!("deep-{i}"))])
        .collect();
    let batch = set_ops::batch::build_batch(&schema, &rows).unwrap();
    let limits = ResourceLimits {
        partition_fanout: 2,
        max_partition_depth: 0,
        ..tight_limits(1 << 20, u64::MAX)
    };
    let spill = tempdir().unwrap();
    let err = execute(
        Query::new(SetOp::Union, Qualifier::Distinct, vec![batch], vec![]),
        limits,
        ExecutionMode::External,
        Some(spill.path()),
        None,
    )
    .unwrap_err();
    assert!(matches!(
        err.kind,
        ErrorKind::ResourceExhausted(ResourceCode::PartitionDepth)
    ));
}

#[test]
fn output_limit_is_enforced() {
    let left = one_col_batch(&std::iter::repeat_n(serde_json::json!(1), 5).collect::<Vec<_>>());
    let limits = ResourceLimits {
        max_output_rows: 3,
        ..ResourceLimits::default()
    };
    let err = execute(
        Query::new(SetOp::Union, Qualifier::All, left, vec![]),
        limits,
        ExecutionMode::InMemory,
        None,
        None,
    )
    .unwrap_err();
    assert!(matches!(
        err.kind,
        ErrorKind::ResourceExhausted(ResourceCode::OutputLimit)
    ));
}

#[test]
fn schema_mismatch_and_bad_values_are_input_errors() {
    let dir = tempdir().unwrap();
    let a = dir.path().join("a.csv");
    let b = dir.path().join("b.csv");
    std::fs::write(&a, "x:bigint\n1\n2\n").unwrap();
    std::fs::write(&b, "x:text\n1\n2\n").unwrap();
    let err = run_pair_files(
        &a,
        &b,
        SetOp::Union,
        Qualifier::All,
        ExecutionMode::InMemory,
        ResourceLimits::default(),
        None,
    )
    .unwrap_err();
    assert!(matches!(
        err.kind,
        ErrorKind::Input(InputCode::SchemaMismatch)
    ));

    std::fs::write(&a, "x:bigint\nnope\n").unwrap();
    let err = read_typed_csv_batches(&a, 10).unwrap_err();
    assert!(matches!(err.kind, ErrorKind::Input(InputCode::ParseValue)));
    assert_eq!(err.context.line, Some(2));
}

#[test]
fn duplicate_run_id_is_a_state_conflict() {
    let spill = tempdir().unwrap();
    let left = one_col_batch(&[serde_json::json!(1)]);
    let q = || Query::new(SetOp::Union, Qualifier::Distinct, left.clone(), vec![]);
    execute(
        q(),
        ResourceLimits::default(),
        ExecutionMode::Auto,
        Some(spill.path()),
        Some("fixed-run-id".into()),
    )
    .unwrap();
    let err = execute(
        q(),
        ResourceLimits::default(),
        ExecutionMode::Auto,
        Some(spill.path()),
        Some("fixed-run-id".into()),
    )
    .unwrap_err();
    assert!(matches!(
        err.kind,
        ErrorKind::StateConflict(set_ops::error::StateCode::RunIdConflict)
    ));
}

#[test]
fn empty_side_follows_multiset_laws_in_all_modes() {
    let dir = tempdir().unwrap();
    let schema = Schema::parse_header("v:bigint").unwrap();
    let nonempty = set_ops::batch::build_batch(&schema, &[json(1), json(1), json(2)]).unwrap();
    let empty = set_ops::batch::build_batch(&schema, &[]).unwrap();

    for mode in [
        ExecutionMode::InMemory,
        ExecutionMode::Auto,
        ExecutionMode::External,
    ] {
        let spill = if matches!(mode, ExecutionMode::InMemory) {
            None
        } else {
            Some(dir.path())
        };
        // L ∪ ∅ = L
        let u = execute(
            Query::new(
                SetOp::Union,
                Qualifier::All,
                vec![nonempty.clone()],
                vec![empty.clone()],
            ),
            tight_limits(128, u64::MAX),
            mode,
            spill,
            None,
        )
        .unwrap();
        assert_eq!(u.stats.output_rows, 3, "{mode:?}");
        // L ∩ ∅ = ∅
        let i = execute(
            Query::new(
                SetOp::Intersect,
                Qualifier::All,
                vec![nonempty.clone()],
                vec![empty.clone()],
            ),
            tight_limits(128, u64::MAX),
            mode,
            spill,
            None,
        )
        .unwrap();
        assert_eq!(i.stats.output_rows, 0, "{mode:?}");
        // L − ∅ = L
        let e = execute(
            Query::new(
                SetOp::Except,
                Qualifier::All,
                vec![nonempty.clone()],
                vec![empty.clone()],
            ),
            tight_limits(128, u64::MAX),
            mode,
            spill,
            None,
        )
        .unwrap();
        assert_eq!(e.stats.output_rows, 3, "{mode:?}");
    }
}

fn json(v: i64) -> Vec<serde_json::Value> {
    vec![serde_json::json!(v)]
}

#[test]
fn runlog_records_replayable_decisions() {
    let dir = tempdir().unwrap();
    let spill = tempdir().unwrap();
    let (lp, rp) = write_stress_pair(dir.path(), 1500, 20);
    let out = run_pair_files(
        &lp,
        &rp,
        SetOp::Except,
        Qualifier::All,
        ExecutionMode::Auto,
        tight_limits(220, u64::MAX),
        Some(spill.path()),
    )
    .unwrap();
    let summary = out.log.replay_summary();
    assert!(summary.spills > 0, "summary: {summary:?}");
    assert_eq!(summary.output_rows, out.stats.output_rows);
    let kinds: Vec<String> = out
        .log
        .events()
        .iter()
        .map(|e| {
            serde_json::to_value(e).unwrap()["event"]
                .as_str()
                .unwrap()
                .to_string()
        })
        .collect();
    assert!(kinds.contains(&"query_start".to_string()));
    assert!(kinds.contains(&"spill".to_string()));
    assert!(kinds.contains(&"count_arithmetic".to_string()));
    assert!(kinds.contains(&"verdict".to_string()));
    // JSONL file exists on disk and is parseable line-by-line.
    let path = spill
        .path()
        .join("logs")
        .join(format!("{}.jsonl", out.stats.run_id));
    let content = std::fs::read_to_string(path).unwrap();
    let lines: Vec<_> = content.lines().collect();
    assert_eq!(lines.len(), out.log.events().len());
    for l in lines {
        let v: serde_json::Value = serde_json::from_str(l).unwrap();
        assert!(v.get("run_id").is_some());
        assert!(v.get("seq").is_some());
    }
}
