use ipc_checker::{verify_full, verify_semantic, VerificationFailure};
use ipc_core::{EngineInput, EngineVerdict, InterpolationEngine, ResolutionBudget};
use ipc_proof::{Clause, Literal, ProofBuilder, Side};
use ipc_syntax::{parse, Formula};

fn engine_prove(a: &str, b: &str) -> (Formula, ipc_proof::Proof) {
    let a = parse(a).unwrap();
    let b = parse(b).unwrap();
    let mut engine = InterpolationEngine::new("independent-check");
    match engine.run(EngineInput {
        a: &a,
        b: &b,
        budget: ResolutionBudget::unlimited(),
    }) {
        EngineVerdict::Proved(proved) => (proved.interpolant, proved.proof),
        other => panic!("expected proof, got {other:?}"),
    }
}

#[test]
fn accepts_engine_output_for_classic_pair() {
    let a = parse("a & (!a | d)").unwrap();
    let b = parse("!d").unwrap();
    let (interpolant, proof) = engine_prove("a & (!a | d)", "!d");
    assert_eq!(interpolant, Formula::var("d"));
    let report = verify_full(&a, &b, &interpolant, &proof);
    assert!(
        report.accepted(),
        "unexpected failures {:?}",
        report.failures
    );
    assert_eq!(report.common_variables, vec!["d".to_string()]);
}

#[test]
fn rejects_interpolant_using_private_variable() {
    let a = parse("x & y").unwrap();
    let b = parse("!y").unwrap();
    let bogus = parse("x & y").unwrap();
    let report = verify_semantic(&a, &b, &bogus);
    assert!(!report.accepted());
    assert!(
        report.failures.iter().any(|failure| matches!(
            failure,
            VerificationFailure::CommonVariableViolation { offenders }
                if offenders == &vec!["x".to_string()]
        )),
        "got {:?}",
        report.failures
    );
}

#[test]
fn rejects_when_antecedent_does_not_imply_candidate() {
    let a = parse("p").unwrap();
    let b = parse("!p").unwrap();
    let bogus = parse("p & q").unwrap();
    let report = verify_semantic(&a, &b, &bogus);
    assert!(report.failures.iter().any(|failure| matches!(
        failure,
        VerificationFailure::CommonVariableViolation { .. }
    )));
    assert!(report.failures.iter().any(|failure| matches!(
        failure,
        VerificationFailure::AntecedentImplicationFailure { .. }
    )));
}

#[test]
fn flags_jointly_satisfiable_pair() {
    let a = parse("p").unwrap();
    let b = parse("q").unwrap();
    let candidate = parse("p").unwrap();
    let report = verify_semantic(&a, &b, &candidate);
    assert!(report.failures.iter().any(|failure| matches!(
        failure,
        VerificationFailure::JointlySatisfiable { .. }
    )));
}

#[test]
fn detects_tampered_resolution_proof() {
    let a = parse("p").unwrap();
    let b = parse("!p").unwrap();
    let candidate = Formula::var("p");
    let mut builder = ProofBuilder::new();
    let pos = builder.add_hypothesis(
        Side::A,
        Clause::unit(Literal::positive("z")),
        "A".into(),
    );
    let neg = builder.add_hypothesis(
        Side::B,
        Clause::unit(Literal::negative("z")),
        "B".into(),
    );
    let root = builder.add_resolution("z".into(), pos, neg, Clause::empty());
    let proof = builder.finish(root);
    let report = verify_full(&a, &b, &candidate, &proof);
    assert!(report.failures.iter().any(|failure| matches!(
        failure,
        VerificationFailure::AnnotationMismatch { .. }
    )));
}
