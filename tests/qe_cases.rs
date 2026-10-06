//! Quantifier-elimination tests: expansion shape, budget contract,
//! capture-avoiding substitution, and proof-trace contents.
use qe_fol::eval::eval_closed;
use qe_fol::model::Model;
use qe_fol::proof::Action;
use qe_fol::qe::{Eliminator, QeStatus};
use qe_fol::run_pipeline;
use qe_fol::syntax::{Formula, Term};

fn var(name: &str) -> Term {
    Term::Var(name.to_string())
}

fn konst(name: &str) -> Term {
    Term::Const(name.to_string())
}

fn model_two() -> Model {
    Model::from_json_str(include_str!("../fixtures/model_two.json")).unwrap()
}

#[test]
fn expansion_produces_expected_quantifier_free_formula() {
    // forall x. (P(x) or Q(x))  ==>  (P(a) or Q(a)) and (P(b) or Q(b))
    let f = Formula::forall(
        "x",
        Formula::or(vec![
            Formula::atom("P", vec![var("x")]),
            Formula::atom("Q", vec![var("x")]),
        ]),
    );
    let outcome = Eliminator::new(&model_two(), None).run(&f).unwrap();
    assert_eq!(outcome.status, QeStatus::Eliminated);
    assert!(!outcome.unknown);
    let expected = Formula::and(vec![
        Formula::or(vec![
            Formula::atom("P", vec![konst("a")]),
            Formula::atom("Q", vec![konst("a")]),
        ]),
        Formula::or(vec![
            Formula::atom("P", vec![konst("b")]),
            Formula::atom("Q", vec![konst("b")]),
        ]),
    ]);
    assert_eq!(outcome.formula, expected);
    // Both sides of the conjunction hold on model "two" (P(a), Q(b)).
    assert_eq!(eval_closed(&model_two(), &outcome.formula).unwrap(), true);
    // Trace: exactly one expand step with a reason and the run id.
    assert_eq!(outcome.trace.len(), 1);
    match &outcome.trace[0].action {
        Action::Expand {
            var, domain_size, ..
        } => {
            assert_eq!(var, "x");
            assert_eq!(*domain_size, 2);
        }
        other => panic!("expected expand step, got {other:?}"),
    }
    assert!(outcome.trace[0].run_id.starts_with("run-"));
    assert!(outcome.trace[0].reason.contains("forall"));
}

#[test]
fn budget_exhaustion_keeps_quantifiers_and_marks_unknown() {
    // forall x. exists y. forall z. (R(x,y) or R(y,z) or x=z)
    let inner = Formula::forall(
        "z",
        Formula::or(vec![
            Formula::atom("R", vec![var("x"), var("y")]),
            Formula::atom("R", vec![var("y"), var("z")]),
            Formula::eq(var("x"), var("z")),
        ]),
    );
    let f = Formula::forall("x", Formula::exists("y", inner));
    // Budget 0: nothing is expanded, formula returned unchanged.
    let outcome = Eliminator::new(&model_two(), Some(0)).run(&f).unwrap();
    assert_eq!(outcome.status, QeStatus::Partial);
    assert!(outcome.unknown);
    assert_eq!(outcome.formula, f);
    assert_eq!(outcome.remaining_quantifiers, 3);
    assert!(outcome
        .trace
        .iter()
        .any(|s| matches!(s.action, Action::BudgetExhausted { .. })));
    // Budget 1: only the outer forall is expanded; each of the 2 copies
    // keeps its exists/forall pair => 4 quantifiers remain.
    let outcome = Eliminator::new(&model_two(), Some(1)).run(&f).unwrap();
    assert_eq!(outcome.status, QeStatus::Partial);
    assert!(outcome.unknown);
    assert_eq!(outcome.remaining_quantifiers, 4);
    assert!(!outcome.formula.is_quantifier_free());
    let exhausted = outcome
        .trace
        .iter()
        .filter(|s| matches!(s.action, Action::BudgetExhausted { .. }))
        .count();
    assert_eq!(exhausted, 2);
    // Unlimited budget: full elimination, and the pipeline checker agrees.
    let report = run_pipeline(&model_two(), &f, None).unwrap();
    assert_eq!(report.status, QeStatus::Eliminated);
    assert!(report.check.ok, "violations: {:?}", report.check.violations);
    // Hand-computed: x=a,y=b gives R(a,b) for all z; x=b has no good y
    // (y=a,z=a: R(b,a) F, R(a,a) F, b=a F) => false.
    assert_eq!(report.value, false);
}

#[test]
fn substitution_avoids_variable_capture() {
    // Substituting variable "a" for x under a binder that binds "a" must
    // rename the binder; a naive substitution would capture.
    let body = Formula::forall("a", Formula::eq(var("x"), var("a")));
    let substituted = body.subst("x", &var("a"));
    match &substituted {
        Formula::Forall { var: bound, body } => {
            assert_eq!(bound, "a#1");
            assert_eq!(
                **body,
                Formula::eq(var("a"), var("a#1")),
                "renamed binder must not capture the substituted variable"
            );
        }
        other => panic!("expected forall, got {other:?}"),
    }
}

#[test]
fn constants_and_variables_have_separate_namespaces() {
    // Substituting the *constant* "a" under a binder for *variable* "a"
    // needs no renaming: the constant cannot be captured.
    let body = Formula::forall("a", Formula::eq(var("x"), var("a")));
    let substituted = body.subst("x", &konst("a"));
    assert_eq!(
        substituted,
        Formula::forall("a", Formula::eq(konst("a"), var("a")))
    );
}

#[test]
fn qe_over_shadowed_and_colliding_names_stays_correct() {
    // forall x. forall a. x=a  over domain {a,b}: the bound variable "a"
    // collides in name with domain element "a". Expansion substitutes
    // constants, so no capture occurs; the checker confirms equivalence.
    let f = Formula::forall(
        "x",
        Formula::forall("a", Formula::eq(var("x"), var("a"))),
    );
    // Budget 1 freezes the intermediate state: the inner binder "a" must
    // survive untouched next to the substituted *constant* "a" (separate
    // namespaces, no capture, no spurious rename).
    let mid = Eliminator::new(&model_two(), Some(1)).run(&f).unwrap();
    assert_eq!(mid.status, QeStatus::Partial);
    let expected_mid = Formula::and(vec![
        Formula::forall("a", Formula::eq(konst("a"), var("a"))),
        Formula::forall("a", Formula::eq(konst("b"), var("a"))),
    ]);
    assert_eq!(mid.formula, expected_mid);
    // Full run: equivalence verified by the independent checker.
    let report = run_pipeline(&model_two(), &f, None).unwrap();
    assert_eq!(report.status, QeStatus::Eliminated);
    assert!(report.check.ok, "violations: {:?}", report.check.violations);
    // Each copy forall a. const(x)=a fails for the other element => false.
    assert_eq!(report.value, false);
}

#[test]
fn empty_domain_expands_to_vacuous_constants() {
    let model = Model::from_json_str(include_str!("../fixtures/model_empty.json")).unwrap();
    let forall_p = Formula::forall("x", Formula::atom("P", vec![var("x")]));
    let exists_p = Formula::exists("x", Formula::atom("P", vec![var("x")]));
    let out_forall = Eliminator::new(&model, None).run(&forall_p).unwrap();
    assert_eq!(out_forall.formula, Formula::True);
    assert!(out_forall
        .trace
        .iter()
        .any(|s| matches!(s.action, Action::EmptyDomainExpansion { .. })));
    let out_exists = Eliminator::new(&model, None).run(&exists_p).unwrap();
    assert_eq!(out_exists.formula, Formula::False);
}

#[test]
fn pipeline_report_carries_run_id_through_trace_and_check() {
    let f = Formula::exists("x", Formula::atom("P", vec![var("x")]));
    let report = run_pipeline(&model_two(), &f, Some(8)).unwrap();
    assert!(report.run_id.starts_with("run-"));
    assert!(!report.trace.is_empty());
    assert!(report.trace.iter().all(|s| s.run_id == report.run_id));
    assert_eq!(report.check.run_id, report.run_id);
    assert!(report.check.ok);
    assert_eq!(report.value, true); // P(a) holds
}
