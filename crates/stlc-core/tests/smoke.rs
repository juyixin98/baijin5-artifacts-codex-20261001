//! Vertical-slice tests: real assertions on parsed terms, types, reduction
//! step counts and failure categories — not "endpoint callable" checks.

use stlc_core::error::{DriverError, NormError, TypeError};
use stlc_core::{alpha_equivalent, run_source};
use stlc_proof::Binding;
use stlc_syntax::db::to_locally_nameless;
use stlc_syntax::parse::{parse_term, parse_type};
use stlc_syntax::pretty::term_to_string;

fn sig(name: &str, ty: &str) -> Binding {
    Binding {
        name: name.to_string(),
        ty: parse_type(ty).unwrap(),
    }
}

#[test]
fn identity_keeps_type_and_is_normal() {
    let resp = run_source("lam x: Bool. x", vec![], 64).unwrap();
    assert_eq!(format!("{:?}", resp.before_type), "Arrow { domain: Bool, codomain: Bool }");
    assert_eq!(resp.steps_used, 0);
    assert_eq!(resp.free_vars_before, Vec::<String>::new());
    assert!(resp.invariants.iter().all(|r| r.passed));
}

#[test]
fn church_addition_one_plus_one_normalizes_to_two_in_six_steps() {
    // plus = lam m:(Nat->Nat)->Nat->Nat. ... (church numerals)
    let num = "(Nat -> Nat) -> Nat -> Nat";
    let plus = "lam m:".to_string()
        + num
        + ". lam n:"
        + num
        + ". lam s: Nat -> Nat. lam z: Nat. m s (n s z)";
    let one = "lam s: Nat -> Nat. lam z: Nat. s z";
    let two = "lam s: Nat -> Nat. lam z: Nat. s (s z)";
    let term = format!("({plus}) ({one}) ({one})");

    let resp = run_source(&term, vec![], 64).unwrap();

    let expected_nf = parse_term(two).unwrap();
    assert!(
        alpha_equivalent(
            &to_locally_nameless(&resp.normal_form),
            &to_locally_nameless(&expected_nf),
        ),
        "normal form was `{}`",
        term_to_string(&resp.normal_form)
    );
    // Leftmost-innermost order contracts both `1 s` sub-redexes before the
    // outer m/n redexes: two numeral bodies plus two outer contractions = 6.
    assert_eq!(resp.steps_used, 6, "leftmost-innermost step trace: {resp:?}");
    assert_eq!(resp.free_vars_before, Vec::<String>::new());
    assert_eq!(resp.free_vars_after, Vec::<String>::new());
    assert!(resp.invariants.iter().all(|r| r.passed));
}

#[test]
fn free_variable_is_tracked_through_reduction() {
    // (lam f: Bool -> Bool. f) applied to a free variable requires the
    // variable to carry the right declared type.
    let term = "(lam f: Bool -> Bool. f) g";
    let resp = run_source(term, vec![sig("g", "Bool -> Bool")], 16).unwrap();
    assert_eq!(resp.free_vars_before, vec!["g".to_string()]);
    assert_eq!(resp.free_vars_after, vec!["g".to_string()]);
    assert_eq!(resp.steps_used, 1);
}

#[test]
fn applying_non_function_is_a_type_error_not_a_budget_error() {
    let err = run_source("true false", vec![], 16).unwrap_err();
    match err {
        DriverError::Check(NormError::Type(TypeError::ExpectedFunction { found })) => {
            assert_eq!(format!("{found:?}"), "Bool");
        }
        other => panic!("expected expected_function type error, got {other:?}"),
    }
}

#[test]
fn domain_mismatch_reports_conflicting_types() {
    let bad = run_source("(lam x: Bool. x) (lam b: Bool. b)", vec![], 16).unwrap_err();
    match bad {
        DriverError::Check(NormError::Type(TypeError::DomainMismatch {
            expected,
            found,
        })) => {
            assert_eq!(format!("{expected:?}"), "Bool");
            assert_eq!(
                format!("{found:?}"),
                "Arrow { domain: Bool, codomain: Bool }"
            );
        }
        other => panic!("expected domain_mismatch, got {other:?}"),
    }
}

#[test]
fn unknown_free_variable_is_distinct_failure() {
    let err = run_source("(lam f: Bool. f) y", vec![], 16).unwrap_err();
    assert!(matches!(
        err,
        DriverError::Check(NormError::Type(TypeError::UnknownFreeVar { .. }))
    ));
}

#[test]
fn budget_exhaustion_is_separate_from_type_errors() {
    // A well-typed term that cannot finish in the given tiny budget:
    // chain of redexes (lam x:B. x) ((lam x:B. x) true) -> needs 2 steps.
    let term = "(lam x: Bool. x) ((lam x: Bool. x) true)";
    let err = run_source(term, vec![], 1).unwrap_err();
    match err {
        DriverError::Check(NormError::BudgetExhausted {
            limit,
            steps_used,
            ..
        }) => {
            assert_eq!(limit, 1);
            assert_eq!(steps_used, 1);
        }
        other => panic!("expected budget_exhausted, got {other:?}"),
    }
    // With enough budget it succeeds and normalizes to `true`.
    let resp = run_source(term, vec![], 16).unwrap();
    assert_eq!(term_to_string(&resp.normal_form), "true");
    assert_eq!(resp.steps_used, 2);
}

#[test]
fn if_elimination_reduces_and_checks_branches() {
    let resp = run_source("if true then false else true", vec![], 16).unwrap();
    assert_eq!(term_to_string(&resp.normal_form), "false");
    assert_eq!(resp.steps_used, 1);

    let err = run_source("if true then false else (lam x: Bool. x)", vec![], 16).unwrap_err();
    assert!(matches!(
        err,
        DriverError::Check(NormError::Type(TypeError::BranchMismatch { .. }))
    ));
}

#[test]
fn parse_failures_are_reported_with_offset() {
    let err = run_source("lam x: . x", vec![], 16).unwrap_err();
    assert!(matches!(err, DriverError::Parse { .. }));
}
