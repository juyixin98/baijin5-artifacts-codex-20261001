//! Trace persistence and replay tests: run id stability, intermediate
//! state capture, categorized failure records, and successful replay
//! reproduction. Also verifies the arrow2 pair chunk shape.

use std::path::PathBuf;

use iejoin::dto::{JoinRequest, PredicateDto};
use iejoin::engine;
use iejoin::error::ErrorCategory;
use iejoin::fixtures as fx;
use iejoin::trace::{self, Outcome, Tracer};

fn temp_dir(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "iejoin-test-{}-{}-{}",
        tag,
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn selective_request(budget_max: Option<u64>) -> JoinRequest {
    let sc = fx::by_name("selective").unwrap();
    JoinRequest {
        left: fx::batch_to_dto(&sc.left),
        right: fx::batch_to_dto(&sc.right),
        predicates: vec![
            PredicateDto {
                left_column: "x".to_owned(),
                op: "<".to_owned(),
                right_column: "x".to_owned(),
            },
            PredicateDto {
                left_column: "y".to_owned(),
                op: "<".to_owned(),
                right_column: "y".to_owned(),
            },
        ],
        budget: budget_max.map(|m| iejoin::resource::Budget {
            max_output_pairs: m,
            ..iejoin::resource::Budget::default()
        }),
        include_trace: true,
    }
}

#[test]
fn successful_run_writes_replayable_trace_with_run_id() {
    let dir = temp_dir("ok");
    let tracer = Tracer::new(Some(dir.clone()));
    let parsed = selective_request(None).parse().unwrap();
    let out = engine::execute_oneshot(parsed, &tracer).unwrap();

    assert!(out.run_id.starts_with("run-"));
    let path = dir.join(format!("{}.json", out.run_id));
    assert!(path.exists(), "trace file must be persisted");

    let rec = trace::load(&path).unwrap();
    assert_eq!(rec.run_id, out.run_id);
    assert_eq!(rec.outcome, Outcome::Completed);
    assert_eq!(rec.pairs_emitted, 5);
    assert_eq!(
        rec.counters.candidate_accesses,
        fx::SELECTIVE_EXPECTED_CANDIDATE_ACCESSES
    );
    // Key intermediate state is present: at least one right_row event
    // carrying gate cursor and pred2 bound.
    let ev = rec
        .events
        .iter()
        .find(|e| e.kind == "right_row")
        .expect("intermediate right_row event recorded");
    assert!(ev.rationale.contains("pred2 prefix"));
    assert!(ev.state.get("bound2").is_some());
    assert!(ev.state.get("gate_cursor").is_some());

    // Input summary identifies the scenario for replay.
    assert_eq!(rec.input_summary["left_rows"], 100);
    assert_eq!(rec.input_summary["right_rows"], 1);

    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn truncated_run_is_recorded_as_truncated_outcome() {
    let dir = temp_dir("trunc");
    let tracer = Tracer::new(Some(dir.clone()));
    let parsed = selective_request(Some(3)).parse().unwrap();
    let out = engine::execute_oneshot(parsed, &tracer).unwrap();
    assert!(out.truncated);

    let path = dir.join(format!("{}.json", out.run_id));
    let rec = trace::load(&path).unwrap();
    assert_eq!(rec.outcome, Outcome::Truncated);
    assert_eq!(rec.pairs_emitted, 3);
    assert_eq!(rec.budget.max_output_pairs, 3);

    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn failed_run_records_category_code_and_run_id() {
    let dir = temp_dir("fail");
    let tracer = Tracer::new(Some(dir.clone()));
    // Engine-level failure: an unknown key column passes DTO parsing but
    // fails during preparation inside the engine.
    let mut bad = selective_request(None);
    bad.predicates[0].left_column = "missing".to_owned();
    let parsed = bad.parse().unwrap();
    let e = engine::execute_oneshot(parsed, &tracer).unwrap_err();
    assert_eq!(e.category, ErrorCategory::Input);
    assert_eq!(e.code, "unknown_column");
    let run_id = e.details.get("run_id").unwrap().as_str().unwrap();

    let path = dir.join(format!("{run_id}.json"));
    let rec = trace::load(&path).unwrap();
    assert_eq!(rec.outcome, Outcome::Failed);
    assert_eq!(rec.error.as_ref().unwrap().code, "unknown_column");
    assert_eq!(rec.error.as_ref().unwrap().category, "input");

    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn replay_reproduces_recorded_counters() {
    let dir = temp_dir("replay");
    let tracer = Tracer::new(Some(dir.clone()));
    let parsed = selective_request(None).parse().unwrap();
    let first = engine::execute_oneshot(parsed, &tracer).unwrap();
    let path = dir.join(format!("{}.json", first.run_id));
    let rec = trace::load(&path).unwrap();

    // Re-run with fresh tracer but identical inputs: counters reproduce.
    let tracer2 = Tracer::new(None);
    let second =
        engine::execute_oneshot(selective_request(None).parse().unwrap(), &tracer2).unwrap();
    assert_eq!(
        rec.counters.candidate_accesses,
        second.counters.candidate_accesses
    );
    assert_eq!(rec.pairs_emitted, second.pairs.len() as u64);

    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn malformed_trace_file_is_compute_error() {
    let dir = temp_dir("badtrace");
    let path = dir.join("x.json");
    std::fs::write(&path, b"{ not json").unwrap();
    let e = trace::load(&path).unwrap_err();
    assert_eq!(e.category, ErrorCategory::Compute);
    assert_eq!(e.code, "trace_parse_failed");
    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn arrow_pair_chunk_has_expected_schema_and_rows() {
    let tracer = Tracer::new(None);
    let out = engine::execute_oneshot(selective_request(None).parse().unwrap(), &tracer).unwrap();
    let chunk = &out.chunk;
    assert_eq!(chunk.len(), 5);
    let fields = iejoin::engine::pairs_schema();
    assert_eq!(fields.fields.len(), 4);
    assert_eq!(
        chunk.arrays()[0].data_type(),
        &arrow2::datatypes::DataType::Utf8
    );
    assert_eq!(
        chunk.arrays()[2].data_type(),
        &arrow2::datatypes::DataType::Int64
    );
}
