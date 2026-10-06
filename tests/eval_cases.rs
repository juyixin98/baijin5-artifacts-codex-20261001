//! Model-checking tests with hand-written reference answers.

use qe_fol::error::ErrorKind;
use qe_fol::eval::eval_closed;
use qe_fol::model::Model;
use qe_fol::syntax::{Formula, Term};

fn var(name: &str) -> Term {
    Term::Var(name.to_string())
}

fn model_two() -> Model {
    Model::from_json_str(include_str!("../fixtures/model_two.json")).unwrap()
}

#[test]
fn alternating_forall_exists_is_false() {
    // forall x. exists y. R(x, y)
    // x=a: R(a,b) holds. x=b: no tuple (b, _) exists. => false
    let f = Formula::forall(
        "x",
        Formula::exists("y", Formula::atom("R", vec![var("x"), var("y")])),
    );
    assert_eq!(eval_closed(&model_two(), &f).unwrap(), false);
}

#[test]
fn alternating_exists_forall_is_true() {
    // exists x. forall y. not R(y, x)
    // x=a: neither R(a,a) nor R(b,a) holds. => true
    let f = Formula::exists(
        "x",
        Formula::forall(
            "y",
            Formula::not(Formula::atom("R", vec![var("y"), var("x")])),
        ),
    );
    assert_eq!(eval_closed(&model_two(), &f).unwrap(), true);
}

#[test]
fn shadowed_variable_inner_binder_wins() {
    // exists x. (P(x) and forall x. Q(x))
    // P(a) holds and Q holds on all elements. => true
    let f = Formula::exists(
        "x",
        Formula::and(vec![
            Formula::atom("P", vec![var("x")]),
            Formula::forall("x", Formula::atom("Q", vec![var("x")])),
        ]),
    );
    assert_eq!(eval_closed(&model_two(), &f).unwrap(), true);
}

#[test]
fn shadowed_variable_negative_case() {
    // exists x. (P(x) and forall x. not P(x))
    // forall x. not P(x) is false because P(a) holds. => false
    let f = Formula::exists(
        "x",
        Formula::and(vec![
            Formula::atom("P", vec![var("x")]),
            Formula::forall(
                "x",
                Formula::not(Formula::atom("P", vec![var("x")])),
            ),
        ]),
    );
    assert_eq!(eval_closed(&model_two(), &f).unwrap(), false);
}

#[test]
fn empty_relation_exists_is_false_forall_is_true() {
    let model = model_two();
    let exists_e = Formula::exists("x", Formula::atom("E", vec![var("x")]));
    let forall_not_e = Formula::forall(
        "x",
        Formula::not(Formula::atom("E", vec![var("x")])),
    );
    assert_eq!(eval_closed(&model, &exists_e).unwrap(), false);
    assert_eq!(eval_closed(&model, &forall_not_e).unwrap(), true);
}

#[test]
fn empty_domain_policy_is_explicit() {
    // allow_empty_domain = true: vacuous truth rules apply.
    let model = Model::from_json_str(include_str!("../fixtures/model_empty.json")).unwrap();
    let forall_p = Formula::forall("x", Formula::atom("P", vec![var("x")]));
    let exists_p = Formula::exists("x", Formula::atom("P", vec![var("x")]));
    assert_eq!(eval_closed(&model, &forall_p).unwrap(), true);
    assert_eq!(eval_closed(&model, &exists_p).unwrap(), false);

    // allow_empty_domain = false: loading the model is a state conflict.
    let err = Model::from_json_str(include_str!("../fixtures/model_empty_forbidden.json"))
        .unwrap_err();
    assert_eq!(err.kind, ErrorKind::StateConflict);
}

#[test]
fn free_variable_is_state_conflict() {
    let model = model_two();
    let f = Formula::atom("P", vec![var("x")]);
    let err = eval_closed(&model, &f).unwrap_err();
    assert_eq!(err.kind, ErrorKind::StateConflict);
}
