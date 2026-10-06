//! Failure-category tests: each error kind is triggered on purpose and
//! asserted by exact category, not just "the call failed".
use qe_fol::check::verify_outcome;
use qe_fol::error::ErrorKind;
use qe_fol::eval::eval_closed;
use qe_fol::model::Model;
use qe_fol::qe::{Eliminator, QeStatus};
use qe_fol::syntax::{Formula, Term};
fn var(name: &str) -> Term {
    Term::Var(name.to_string())
}

fn model_two() -> Model {
    Model::from_json_str(include_str!("../fixtures/model_two.json")).unwrap()
}

#[test]
fn malformed_formula_json_is_invalid_input() {
    let err = Formula::from_json_str("{\"op\": \"forall\", \"var\": 42}").unwrap_err();
    assert_eq!(err.kind, ErrorKind::InvalidInput);
}

#[test]
fn unknown_predicate_is_invalid_input() {
    let f = Formula::atom("Nope", vec![var("x")]);
    let f = Formula::forall("x", f);
    let err = eval_closed(&model_two(), &f).unwrap_err();
    assert_eq!(err.kind, ErrorKind::InvalidInput);
}

#[test]
fn constant_outside_domain_is_invalid_input() {
    let f = Formula::eq(Term::Const("zzz".to_string()), Term::Const("a".to_string()));
    let err = eval_closed(&model_two(), &f).unwrap_err();
    assert_eq!(err.kind, ErrorKind::InvalidInput);
}

#[test]
fn tuple_arity_mismatch_is_invalid_input() {
    let json = r#"{
        "name": "bad-arity",
        "allow_empty_domain": false,
        "domain": ["a"],
        "predicates": [ { "name": "P", "arity": 2, "tuples": [["a"]] } ]
    }"#;
    let err = Model::from_json_str(json).unwrap_err();
    assert_eq!(err.kind, ErrorKind::InvalidInput);
}

#[test]
fn tuple_referencing_unknown_element_is_invalid_input() {
    let json = r#"{
        "name": "bad-element",
        "allow_empty_domain": false,
        "domain": ["a"],
        "predicates": [ { "name": "P", "arity": 1, "tuples": [["ghost"]] } ]
    }"#;
    let err = Model::from_json_str(json).unwrap_err();
    assert_eq!(err.kind, ErrorKind::InvalidInput);
}

#[test]
fn empty_domain_without_policy_is_state_conflict() {
    let err = Model::from_json_str(include_str!("../fixtures/model_empty_forbidden.json"))
        .unwrap_err();
    assert_eq!(err.kind, ErrorKind::StateConflict);
}

#[test]
fn duplicate_domain_element_is_state_conflict() {
    let json = r#"{
        "name": "dup",
        "allow_empty_domain": false,
        "domain": ["a", "a"],
        "predicates": []
    }"#;
    let err = Model::from_json_str(json).unwrap_err();
    assert_eq!(err.kind, ErrorKind::StateConflict);
}

#[test]
fn duplicate_predicate_is_state_conflict() {
    let json = r#"{
        "name": "dup-pred",
        "allow_empty_domain": false,
        "domain": ["a"],
        "predicates": [
            { "name": "P", "arity": 1, "tuples": [] },
            { "name": "P", "arity": 1, "tuples": [["a"]] }
        ]
    }"#;
    let err = Model::from_json_str(json).unwrap_err();
    assert_eq!(err.kind, ErrorKind::StateConflict);
}

#[test]
fn free_variables_block_elimination_with_state_conflict() {
    let f = Formula::and(vec![
        Formula::atom("P", vec![var("x")]),
        Formula::forall("y", Formula::atom("Q", vec![var("y")])),
    ]);
    let err = Eliminator::new(&model_two(), None).run(&f).unwrap_err();
    assert_eq!(err.kind, ErrorKind::StateConflict);
}

#[test]
fn deep_formula_evaluation_is_resource_exhausted() {
    let mut f = Formula::atom("P", vec![Term::Const("a".to_string())]);
    for _ in 0..300 {
        f = Formula::not(f);
    }
    let err = eval_closed(&model_two(), &f).unwrap_err();
    assert_eq!(err.kind, ErrorKind::ResourceExhausted);
}

#[test]
fn deep_quantifier_nesting_elimination_is_resource_exhausted() {
    let mut f = Formula::atom("P", vec![var("v0")]);
    for i in 0..200 {
        let binder = format!("v{i}");
        f = if i % 2 == 0 {
            Formula::forall(&binder, f)
        } else {
            Formula::exists(&binder, f)
        };
    }
    let err = Eliminator::new(&model_two(), None).run(&f).unwrap_err();
    assert_eq!(err.kind, ErrorKind::ResourceExhausted);
}

#[test]
fn checker_flags_forged_outcome_as_failed_computation() {
    // The original formula is true on model "two"; a forged outcome claims
    // the elimination produced `false`. The independent checker must catch
    // the value mismatch.
    let original = Formula::exists("x", Formula::atom("P", vec![var("x")]));
    let forged = qe_fol::qe::QeOutcome {
        run_id: "run-forged-0000".to_string(),
        status: QeStatus::Eliminated,
        unknown: false,
        formula: Formula::False,
        remaining_quantifiers: 0,
        trace: Vec::new(),
    };
    let report = verify_outcome(&model_two(), &original, &forged).unwrap();
    assert!(!report.ok);
    assert!(report
        .violations
        .iter()
        .any(|v| v.contains("value mismatch")));
}
