//! Independent integration tests.
//!
//! Expected answers below are hand-derived truth tables, not produced by the
//! engine under test. Each scenario cross-checks three implementations:
//! bounded expansion + ground evaluation, direct recursion over quantifiers
//! (independent path), and the proof checker's own reference evaluation.

use std::collections::BTreeMap;
use std::path::PathBuf;

use fologic::checker::{check, direct_truth};
use fologic::error::ErrorKind;
use fologic::model::Model;
use fologic::proof::Verdict;
use fologic::service::{run_batch, BatchRequest};

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn load_fixture(name: &str) -> BatchRequest {
    let path = repo_root().join("fixtures/requests").join(name);
    let bytes = std::fs::read(path).unwrap();
    serde_json::from_slice(&bytes).unwrap()
}

fn model() -> Model {
    let bytes = std::fs::read(repo_root().join("fixtures/model/finite_model.json")).unwrap();
    serde_json::from_slice(&bytes).unwrap()
}

#[test]
fn all_golden_scenarios_have_concrete_results() {
    struct Case {
        file: &'static str,
        verdict: Verdict,
        used: u64,
        residual: usize,
        // Independent hand-derived truth, or None when budget forces unknown.
        reference: Option<bool>,
    }

    let cases = [
        Case {
            file: "req_success_f03.json",
            verdict: Verdict::False,
            used: 9,
            residual: 0,
            reference: Some(false),
        },
        Case {
            file: "req_success_f04.json",
            verdict: Verdict::False,
            used: 8,
            residual: 0,
            reference: Some(false),
        },
        Case {
            file: "req_success_shadow.json",
            verdict: Verdict::True,
            used: 12,
            residual: 0,
            reference: Some(true),
        },
        Case {
            file: "req_empty_allowed.json",
            verdict: Verdict::True,
            used: 0,
            residual: 0,
            reference: Some(true),
        },
        Case {
            file: "req_empty_exists_allowed.json",
            verdict: Verdict::False,
            used: 0,
            residual: 0,
            reference: Some(false),
        },
        Case {
            file: "req_budget_mid.json",
            verdict: Verdict::Unknown,
            used: 0,
            residual: 1,
            reference: Some(false),
        },
        Case {
            file: "req_budget_zero.json",
            verdict: Verdict::Unknown,
            used: 0,
            residual: 1,
            reference: Some(true),
        },
        Case {
            file: "req_budget_exact.json",
            verdict: Verdict::False,
            used: 9,
            residual: 0,
            reference: Some(false),
        },
    ];

    let m = model();
    for case in cases {
        let response = run_batch(load_fixture(case.file)).unwrap_or_else(|e| {
            panic!("{} unexpectedly failed: {:?}", case.file, e);
        });
        assert_eq!(response.verdict, case.verdict, "verdict for {}", case.file);
        assert_eq!(
            response.instantiations_used, case.used,
            "instantiation count for {}",
            case.file
        );
        assert_eq!(
            response.residual_quantifiers, case.residual,
            "residual count for {}",
            case.file
        );
        let report = response.check_report.as_ref().expect("checker ran");
        assert!(report.ok, "checker accepted proof for {}", case.file);

        // Fully independent recursion over the ORIGINAL formula.
        let direct = direct_truth(&m, &response.proof.original_formula, true).unwrap();
        assert_eq!(
            Some(direct),
            case.reference,
            "independent reference truth for {}",
            case.file
        );
    }
}

#[test]
fn alternating_quantifiers_match_hand_truth_tables() {
    let m = model();
    // f03: ∀x∈X ∃y∈Y q(x,y). Truth table: x1->y1 yes, x2->y2 yes, x3->none => false.
    let f03: fologic::syntax::Formula = serde_json::from_str(
        &std::fs::read_to_string(repo_root().join("fixtures/formulas/f03_alt_true.json")).unwrap(),
    )
    .unwrap();
    assert!(!direct_truth(&m, &f03, false).unwrap());

    // f04: ∃y∈Y ∀x∈X q(x,y): neither y1 nor y2 covers all three x => false.
    let f04: fologic::syntax::Formula = serde_json::from_str(
        &std::fs::read_to_string(repo_root().join("fixtures/formulas/f04_alt_false.json")).unwrap(),
    )
    .unwrap();
    assert!(!direct_truth(&m, &f04, false).unwrap());

    // Expanded ground evaluation agrees with direct recursion for both.
    for (file, expected) in [
        ("f01_all_p.json", false),
        ("f02_ex_p.json", true),
        ("f03_alt_true.json", false),
        ("f04_alt_false.json", false),
        ("f05_shadow.json", true),
        ("f08_cycle.json", true),
        ("f09_next_const.json", true),
    ] {
        let formula: fologic::syntax::Formula = serde_json::from_str(
            &std::fs::read_to_string(repo_root().join("fixtures/formulas").join(file)).unwrap(),
        )
        .unwrap();
        let direct = direct_truth(&m, &formula, false).unwrap();
        assert_eq!(direct, expected, "direct truth for {}", file);

        let response = fologic::service::run_check(
            &m,
            fologic::service::CheckRequest {
                run_id: Some(format!("cross-{file}")),
                model_name: Some("golden".into()),
                formula,
                budget: Some(1000),
                node_cap: 100_000,
                allow_empty_domain: false,
                verify: true,
            },
        )
        .unwrap();
        assert_eq!(
            response.verdict,
            if expected {
                Verdict::True
            } else {
                Verdict::False
            },
            "expansion truth for {}",
            file
        );
    }
}

#[test]
fn empty_domain_policy_is_explicit_and_distinguished() {
    let m = model();
    let f: fologic::syntax::Formula = serde_json::from_str(
        &std::fs::read_to_string(repo_root().join("fixtures/formulas/f06_empty_forall.json"))
            .unwrap(),
    )
    .unwrap();

    let allowed = fologic::service::run_check(
        &m,
        fologic::service::CheckRequest {
            run_id: Some("empty-on".into()),
            model_name: None,
            formula: f.clone(),
            budget: None,
            node_cap: 1000,
            allow_empty_domain: true,
            verify: true,
        },
    )
    .unwrap();
    assert_eq!(allowed.verdict, Verdict::True);

    let denied = fologic::service::run_check(
        &m,
        fologic::service::CheckRequest {
            run_id: Some("empty-off".into()),
            model_name: None,
            formula: f,
            budget: None,
            node_cap: 1000,
            allow_empty_domain: false,
            verify: false,
        },
    )
    .unwrap_err();
    assert_eq!(denied.kind, ErrorKind::ComputationFailed);
    assert_eq!(denied.code, "empty_domain");
}

#[test]
fn budget_shortage_keeps_quantifier_and_marks_unknown() {
    let response = run_batch(load_fixture("req_budget_mid.json")).unwrap();
    assert_eq!(response.verdict, Verdict::Unknown);
    assert_eq!(response.residual_quantifiers, 1);
    // The residual formula must literally still be the original root sentence.
    let req = load_fixture("req_budget_mid.json");
    let m = model();
    let formula = fologic::service::load_formula(&req).unwrap();
    let resolved = m.check_formula(&formula).unwrap();
    assert_eq!(response.expanded_formula, resolved);

    // Trace records the run id, the exhausted event and a rollback for replay.
    let events: Vec<_> = response
        .proof
        .steps
        .iter()
        .map(|s| match s {
            fologic::proof::Step::BudgetExhausted { .. } => "exhausted",
            fologic::proof::Step::RollbackInstances { .. } => "rollback",
            _ => "other",
        })
        .collect();
    assert!(events.contains(&"exhausted"));
    assert!(events.contains(&"rollback"));
}

#[test]
fn error_classes_are_distinct_and_specific() {
    struct ErrCase {
        file: &'static str,
        kind: ErrorKind,
        code: &'static str,
    }
    let cases = [
        ErrCase {
            file: "req_unknown_symbol.json",
            kind: ErrorKind::Input,
            code: "unknown_symbol",
        },
        ErrCase {
            file: "req_arity.json",
            kind: ErrorKind::Input,
            code: "arity_mismatch",
        },
        ErrCase {
            file: "req_unbound.json",
            kind: ErrorKind::Input,
            code: "unbound_variable",
        },
        ErrCase {
            file: "req_free_var.json",
            kind: ErrorKind::Input,
            code: "unbound_variable",
        },
        ErrCase {
            file: "req_node_cap.json",
            kind: ErrorKind::ResourceExhausted,
            code: "node_cap",
        },
        ErrCase {
            file: "req_empty_denied.json",
            kind: ErrorKind::ComputationFailed,
            code: "empty_domain",
        },
    ];
    for c in cases {
        let err = run_batch(load_fixture(c.file)).unwrap_err();
        assert_eq!(err.kind, c.kind, "kind for {}", c.file);
        assert_eq!(err.code, c.code, "code for {}", c.file);
    }

    // A genuinely missing fixture file is an input error, not an IO panic.
    let mut req = load_fixture("req_success_f03.json");
    req.model_path = Some(repo_root().join("fixtures/model/does_not_exist.json"));
    let err = run_batch(req).unwrap_err();
    assert_eq!(err.kind, ErrorKind::Input);
    assert_eq!(err.code, "fixture_unreadable");
}

#[test]
fn checker_detects_tampered_verdict_as_state_conflict() {
    let m = model();
    // Obtain a valid record, then tamper with its claimed verdict.
    let response = run_batch(load_fixture("req_success_f04.json")).unwrap();
    let mut tampered = response.proof.clone();
    tampered.verdict = Verdict::True; // real reference truth is false
    let err = check(&m, &tampered).unwrap_err();
    assert_eq!(err.kind, ErrorKind::StateConflict);
    assert_eq!(err.code, "proof_check_failed");
    assert!(err.message.contains("verdict_matches_reference"));
}

#[test]
fn checker_detects_tampered_instantiation_count() {
    let m = model();
    let response = run_batch(load_fixture("req_success_f04.json")).unwrap();
    let mut tampered = response.proof.clone();
    tampered.instantiations_used += 1;
    let err = check(&m, &tampered).unwrap_err();
    assert_eq!(err.kind, ErrorKind::StateConflict);
    assert!(err.message.contains("instantiation_accounting"));
}

#[test]
fn partial_function_undefined_tuple_is_computation_failure() {
    // `pair(x3,y2)` is deliberately omitted from the model.
    let m = model();
    use fologic::syntax::build::*;
    let f = eq(
        fologic::syntax::build::app("pair", vec![elem_typed("x3", "X"), elem_typed("y2", "Y")]),
        elem_typed("x1", "X"),
    );
    let err = fologic::service::run_check(
        &m,
        fologic::service::CheckRequest {
            run_id: Some("partial".into()),
            model_name: None,
            formula: f,
            budget: None,
            node_cap: 1000,
            allow_empty_domain: false,
            verify: false,
        },
    )
    .unwrap_err();
    assert_eq!(err.kind, ErrorKind::ComputationFailed);
    assert_eq!(err.code, "partial_function");
}

#[test]
fn run_ids_are_unique_and_replayable() {
    let ids: Vec<String> = (0..4)
        .map(|i| {
            let mut req = load_fixture("req_success_f03.json");
            req.rest.run_id = None;
            req.rest.model_name = Some(format!("gen-{i}"));
            run_batch(req).unwrap().run_id
        })
        .collect();
    for w in ids.windows(2) {
        assert_ne!(w[0], w[1]);
    }
    assert!(ids.iter().all(|id| id.starts_with("run-")));
}

#[test]
fn model_validation_rejects_bad_fixture() {
    let bad = r#"{
        "sorts": [{ "name": "X", "elements": ["a", "a"] }],
        "constants": [], "functions": [], "predicates": []
    }"#;
    let m: Model = serde_json::from_str(bad).unwrap();
    let err = m.validate().unwrap_err();
    assert_eq!(err.kind, ErrorKind::Input);
    assert_eq!(err.code, "duplicate_element");
    let _: BTreeMap<String, ()> = BTreeMap::new();
}
