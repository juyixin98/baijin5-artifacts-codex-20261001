//! Independent integration tests for the streaming resolution proof
//! checker. Every test asserts a concrete verdict *and* the concrete
//! failure class / report fields, not merely that the API can be called.
//!
//! Expected answers here are handwritten (inline proofs and fixture files
//! maintained by hand); the generated chain fixture ships with a `.golden`
//! file so its expected verdict does not come from the checker itself.

use rescheck::checker::{CheckReport, Checker, CheckerLimits, FailureClass, Verdict};
use std::fs::File;
use std::io::BufReader;
use std::path::Path;

fn check(proof: &str) -> CheckReport {
    Checker::new(CheckerLimits::default(), "test-inline".to_string())
        .check_stream(BufReader::new(proof.as_bytes()))
}

fn check_with_limits(proof: &str, limits: CheckerLimits) -> CheckReport {
    Checker::new(limits, "test-limits".to_string()).check_stream(BufReader::new(proof.as_bytes()))
}

fn check_fixture(name: &str) -> CheckReport {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("fixtures")
        .join(name);
    let file = File::open(&path).unwrap_or_else(|e| panic!("cannot open {path:?}: {e}"));
    Checker::new(CheckerLimits::default(), format!("test-fixture:{name}"))
        .check_stream(BufReader::new(file))
}

// ---------- Stage 2: per-step parent existence and pivot legality ----------

#[test]
fn valid_handwritten_proof_verifies() {
    let report = check_fixture("valid_simple.proof");
    assert_eq!(report.verdict, Verdict::Verified);
    assert_eq!(report.failure, None);
    assert!(report.uncertainties.is_empty());
    assert_eq!(report.steps_processed, 5);
    // The empty clause is derived at step 5; the log records it.
    assert!(report
        .log
        .iter()
        .any(|e| e.step == Some(5) && e.message.contains("empty clause derived")));
}

#[test]
fn single_step_tampering_is_rejected_as_clause_mismatch() {
    // Same proof as valid_simple but step 4 claims (x3) instead of (x2).
    let report = check_fixture("tampered_resolvent.proof");
    assert_eq!(report.verdict, Verdict::Rejected);
    let failure = report.failure.expect("rejected reports carry a failure");
    assert_eq!(failure.class, FailureClass::ClauseMismatch);
    assert_eq!(failure.step, Some(4));
    assert!(failure.message.contains("claimed resolvent"));
}

#[test]
fn claimed_resolvent_still_containing_pivot_is_rejected() {
    // Step 4 "forgets" to eliminate the pivot: claims (x1 v x2).
    let proof = "c 1 1 0\nc 2 -1 2 0\nr 4 1 2 1 1 2 0\n";
    let report = check(proof);
    assert_eq!(report.verdict, Verdict::Rejected);
    assert_eq!(
        report.failure.unwrap().class,
        FailureClass::PivotNotEliminated
    );
}

#[test]
fn pivot_absent_from_parents_is_rejected() {
    // (x1) and (x2) share no complementary literal on pivot 1.
    let proof = "c 1 1 0\nc 2 2 0\nr 3 1 2 1 2 0\n";
    let report = check(proof);
    assert_eq!(report.verdict, Verdict::Rejected);
    assert_eq!(report.failure.unwrap().class, FailureClass::PivotMissing);
}

#[test]
fn tautological_resolvent_is_rejected() {
    // (x1 v x2) and (~x1 v ~x2): resolving on x1 leaves x2 v ~x2.
    let proof = "c 1 1 2 0\nc 2 -1 -2 0\nr 3 1 2 1 2 -2 0\n";
    let report = check(proof);
    assert_eq!(report.verdict, Verdict::Rejected);
    assert_eq!(
        report.failure.unwrap().class,
        FailureClass::TautologicalResolvent
    );
}

// ---------- Stage 2: deletion must not break later references ----------

#[test]
fn reference_to_deleted_parent_is_rejected() {
    let report = check_fixture("deleted_parent.proof");
    assert_eq!(report.verdict, Verdict::Rejected);
    let failure = report.failure.unwrap();
    assert_eq!(failure.class, FailureClass::DeletedParent);
    assert_eq!(failure.step, Some(4));
    assert!(failure.message.contains("parent 1"));
}

#[test]
fn dangling_parent_reference_is_rejected() {
    let report = check_fixture("dangling_parent.proof");
    assert_eq!(report.verdict, Verdict::Rejected);
    let failure = report.failure.unwrap();
    assert_eq!(failure.class, FailureClass::DanglingParent);
    assert_eq!(failure.step, Some(4));
    assert!(failure.message.contains("parent 9"));
}

#[test]
fn legal_deletion_keeps_proof_verifiable() {
    // Deletes clause 2 after its last use; all later references stay live.
    let report = check_fixture("valid_with_deletion.proof");
    assert_eq!(report.verdict, Verdict::Verified);
    assert_eq!(report.steps_processed, 7);
}

#[test]
fn double_deletion_is_rejected() {
    let proof = "c 1 1 0\nd 1\nd 1\n";
    let report = check(proof);
    assert_eq!(report.verdict, Verdict::Rejected);
    assert_eq!(report.failure.unwrap().class, FailureClass::DeletedParent);
}

// ---------- Stage 3: termination, duplicates, uncertainty ----------

#[test]
fn empty_clause_terminates_verification() {
    // Garbage after the empty clause must not change the verdict: the
    // checker stops as soon as the proof is complete.
    let proof = "c 1 1 0\nc 2 -1 0\nr 3 1 2 1 0\nr 4 1 1 1 1 0\n";
    let report = check(proof);
    assert_eq!(report.verdict, Verdict::Verified);
    assert_eq!(report.steps_processed, 3);
}

#[test]
fn proof_without_empty_clause_is_unverified_not_verified() {
    let report = check_fixture("no_empty_clause.proof");
    assert_eq!(report.verdict, Verdict::Unverified);
    assert_eq!(report.failure, None);
    assert_eq!(report.uncertainties.len(), 1);
    assert!(report.uncertainties[0].contains("empty clause"));
}

#[test]
fn duplicate_literal_in_declared_clause_is_rejected() {
    let report = check_fixture("duplicate_literal.proof");
    assert_eq!(report.verdict, Verdict::Rejected);
    let failure = report.failure.unwrap();
    assert_eq!(failure.class, FailureClass::DuplicateLiteral);
    assert_eq!(failure.step, Some(1));
}

#[test]
fn duplicate_literal_in_claimed_resolvent_is_rejected() {
    let proof = "c 1 1 0\nc 2 -1 2 0\nr 3 1 2 1 2 2 0\n";
    let report = check(proof);
    assert_eq!(report.verdict, Verdict::Rejected);
    assert_eq!(
        report.failure.unwrap().class,
        FailureClass::DuplicateLiteral
    );
}

// ---------- Stage 2: resource exhaustion means unverified, never accepted ----------

#[test]
fn step_limit_exhaustion_is_unverified() {
    let proof = "c 1 1 0\nc 2 -1 0\nr 3 1 2 1 0\n";
    let limits = CheckerLimits {
        max_steps: 2,
        ..CheckerLimits::default()
    };
    let report = check_with_limits(proof, limits);
    assert_eq!(report.verdict, Verdict::Unverified);
    assert_eq!(report.failure, None);
    assert!(report.uncertainties[0].contains("step limit"));
}

#[test]
fn total_literal_limit_exhaustion_is_unverified() {
    let proof = "c 1 1 2 0\nc 2 -1 3 0\n";
    let limits = CheckerLimits {
        max_total_lits: 3,
        ..CheckerLimits::default()
    };
    let report = check_with_limits(proof, limits);
    assert_eq!(report.verdict, Verdict::Unverified);
    assert!(report.uncertainties[0].contains("total stored literals"));
}

#[test]
fn clause_size_limit_exhaustion_is_unverified() {
    let proof = "c 1 1 2 3 0\n";
    let limits = CheckerLimits {
        max_clause_lits: 2,
        ..CheckerLimits::default()
    };
    let report = check_with_limits(proof, limits);
    assert_eq!(report.verdict, Verdict::Unverified);
    assert!(report.uncertainties[0].contains("clause size"));
}

#[test]
fn generated_chain_proof_matches_golden_and_limits_apply() {
    // Expected answer comes from the handwritten .golden file, not from
    // the checker under test.
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures");
    let golden = std::fs::read_to_string(dir.join("generated_chain.golden"))
        .expect("run `cargo run --bin gen_fixtures` first");
    assert!(golden.contains("verified"), "golden file: {golden}");
    assert!(golden.contains("steps=129"), "golden file: {golden}");

    let report = check_fixture("generated_chain.proof");
    assert_eq!(report.verdict, Verdict::Verified);
    assert_eq!(report.steps_processed, 129);

    // The same proof under a tight step budget must be unverified.
    let path = dir.join("generated_chain.proof");
    let file = File::open(&path).unwrap();
    let limits = CheckerLimits {
        max_steps: 100,
        ..CheckerLimits::default()
    };
    let limited =
        Checker::new(limits, "test-chain-limited".to_string()).check_stream(BufReader::new(file));
    assert_eq!(limited.verdict, Verdict::Unverified);
    assert_eq!(limited.steps_processed, 100);
}

// ---------- Interface interpretability ----------

#[test]
fn report_carries_request_identity_version_and_positions() {
    let report = check_fixture("tampered_resolvent.proof");
    assert_eq!(report.request_id, "test-fixture:tampered_resolvent.proof");
    assert_eq!(report.checker_version, env!("CARGO_PKG_VERSION"));
    let failure = report.failure.unwrap();
    assert!(failure.line > 0, "failure carries an input position");
    // Key steps are logged in order with positions.
    assert!(report
        .log
        .iter()
        .all(|e| e.level == "INFO" || e.level == "ERROR"));
    assert!(report.log.iter().any(|e| e.step == Some(4)));
}

#[test]
fn malformed_line_is_rejected_as_parse_error() {
    let proof = "c 1 1 0\nr 2 1\n";
    let report = check(proof);
    assert_eq!(report.verdict, Verdict::Rejected);
    let failure = report.failure.unwrap();
    assert_eq!(failure.class, FailureClass::ParseError);
    assert_eq!(failure.line, 2);
}

#[test]
fn report_serializes_to_json_with_verdict_field() {
    let report = check_fixture("valid_simple.proof");
    let json: serde_json::Value = serde_json::from_str(&report.to_json()).unwrap();
    assert_eq!(json["verdict"], "verified");
    assert_eq!(json["request_id"], report.request_id);
    assert!(json["checker_version"].is_string());
}
