//! Tests over the locally generated fixtures.
//!
//! Expected values are derived by hand from the generator's tables and
//! asserted against both the independent recursive evaluator and the bounded
//! expansion pipeline.

use std::path::PathBuf;

use fologic::checker::direct_truth;
use fologic::model::Model;
use fologic::proof::Verdict;
use fologic::service::{run_check, CheckRequest};

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn load<T: serde::de::DeserializeOwned>(p: PathBuf) -> T {
    serde_json::from_slice(&std::fs::read(p).unwrap()).unwrap()
}

#[test]
fn generated_fixtures_have_expected_truth_and_expansion_agrees() {
    let m: Model = load(root().join("fixtures/gen/model/gen_model.json"));

    // Hand-derived reference answers for the synthetic model.
    let cases: &[(&str, bool, bool)] = &[
        // file, expected truth, allow_empty_domain
        ("g01_forall_exists.json", false, false), // d1 has no edge to any s
        ("g02_shadow.json", true, false),
        ("g03_empty.json", true, true),       // forall over empty sort
        ("g04_succ_cycle.json", true, false), // succ^4 = identity on D
    ];

    for (file, expected, allow_empty) in cases {
        let formula = load(root().join("fixtures/gen/formulas").join(file));
        let direct = direct_truth(&m, &formula, *allow_empty).unwrap();
        assert_eq!(direct, *expected, "direct truth for {}", file);

        let response = run_check(
            &m,
            CheckRequest {
                run_id: Some(format!("gen-{file}")),
                model_name: Some("gen".into()),
                formula,
                budget: Some(1000),
                node_cap: 100_000,
                allow_empty_domain: *allow_empty,
                verify: true,
            },
        )
        .unwrap();
        assert_eq!(
            response.verdict,
            if *expected {
                Verdict::True
            } else {
                Verdict::False
            },
            "expansion verdict for {}",
            file
        );
        assert!(response.check_report.unwrap().ok);
    }
}

#[test]
fn generated_empty_formula_denied_by_policy() {
    let m: Model = load(root().join("fixtures/gen/model/gen_model.json"));
    let formula = load(root().join("fixtures/gen/formulas/g03_empty.json"));
    let err = run_check(
        &m,
        CheckRequest {
            run_id: Some("gen-empty-denied".into()),
            model_name: None,
            formula,
            budget: None,
            node_cap: 1000,
            allow_empty_domain: false,
            verify: false,
        },
    )
    .unwrap_err();
    assert_eq!(err.kind, fologic::error::ErrorKind::ComputationFailed);
    assert_eq!(err.code, "empty_domain");
}
