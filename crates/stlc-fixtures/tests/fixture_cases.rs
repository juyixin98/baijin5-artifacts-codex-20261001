//! Drives every hand-authored/generated fixture and asserts concrete results
//! and specific failure categories. The case id is logged at each decision so
//! logs correlate inputs with verdicts.

use stlc_audit::audit_proof;
use stlc_core::error::{DriverError, NormError, TypeError};
use stlc_core::{alpha_equivalent, run, run_source};
use stlc_fixtures::{Case, ExpectVerdict};
use stlc_syntax::db::to_locally_nameless;
use stlc_syntax::parse::{parse_term, parse_type};
use stlc_syntax::pretty::term_to_string;

fn type_error_kind(err: &TypeError) -> &'static str {
    match err {
        TypeError::UnknownFreeVar { .. } => "unknown_free_var",
        TypeError::ExpectedFunction { .. } => "expected_function",
        TypeError::DomainMismatch { .. } => "domain_mismatch",
        TypeError::IfGuardNotBool { .. } => "if_guard_not_bool",
        TypeError::BranchMismatch { .. } => "branch_mismatch",
        TypeError::DanglingIndex { .. } => "dangling_index",
    }
}

#[test]
fn all_fixture_cases_match_hand_computed_expectations() {
    let cases = Case::load_all().expect("fixtures load");
    let total = cases.len();
    println!("[progress] loaded {total} fixture cases; pid={}", std::process::id());

    for (i, case) in cases.into_iter().enumerate() {
        let id = &case.id;
        println!("[{}/{}] case={id} :: {}", i + 1, total, case.description);
        println!("  input: {}", case.term_source);
        println!("  hand-reasoning: {}", case.manual_reasoning);

        // Parse first so malformed-input fixtures exercise the parse category.
        let parsed = parse_term(&case.term_source);
        let result = match parsed {
            Ok(term) => run(stlc_core::CheckRequest {
                term,
                free_signature: case.free_signature.clone(),
                budget: case.budget,
            }),
            Err(e) => Err(DriverError::Parse {
                message: e.message,
                offset: e.offset,
            }),
        };
        let _ = run_source; // helper also exists for ad-hoc use

        match (case.expected.verdict, result) {
            (ExpectVerdict::Ok, Ok(resp)) => {
                if let Some(expected_steps) = case.expected.steps {
                    assert_eq!(
                        resp.steps_used, expected_steps,
                        "[{id}] step count differs from hand calc"
                    );
                    println!(
                        "  decision: OK steps={} budget={} (hand calc: {expected_steps})",
                        resp.steps_used, resp.budget
                    );
                } else {
                    println!(
                        "  decision: OK steps={} budget={}",
                        resp.steps_used, resp.budget
                    );
                }
                if let Some(expected_nf) = &case.expected.normal_form {
                    let expected_term = parse_term(expected_nf)
                        .unwrap_or_else(|e| panic!("[{id}] bad expected normal_form: {e}"));
                    assert!(
                        alpha_equivalent(
                            &to_locally_nameless(&resp.normal_form),
                            &to_locally_nameless(&expected_term)
                        ),
                        "[{id}] nf `{}` not alpha-equiv to hand result `{expected_nf}`",
                        term_to_string(&resp.normal_form)
                    );
                    println!("  decision: normal form matches hand result (alpha-equiv)");
                }
                assert_eq!(
                    resp.free_vars_after, case.expected.free_vars,
                    "[{id}] free variables differ"
                );
                if let Some(ty_src) = &case.expected.result_type {
                    let expected_ty = parse_type(ty_src).unwrap();
                    assert_eq!(
                        &resp.after_type, &expected_ty,
                        "[{id}] result type {ty_src} vs {:?}",
                        resp.after_type
                    );
                }
                assert!(
                    resp.invariants.iter().all(|r| r.passed),
                    "[{id}] core invariants failed: {:?}",
                    resp.invariants
                );

                let report = audit_proof(&resp.proof);
                assert!(
                    report.valid,
                    "[{id}] independent audit rejected proof: {report:?}"
                );
                println!(
                    "  decision: independent audit valid ({} nodes)",
                    report.nodes_checked
                );
            }
            (ExpectVerdict::TypeError, Err(DriverError::Check(NormError::Type(err)))) => {
                let got = type_error_kind(&err);
                let want = case.expected.type_error_kind.as_deref().unwrap_or("<none>");
                println!("  decision: TYPE_ERROR kind={got} (expected {want}): {err}");
                assert_eq!(got, want, "[{id}] type error category mismatch");
            }
            (
                ExpectVerdict::BudgetExhausted,
                Err(DriverError::Check(NormError::BudgetExhausted {
                    limit,
                    steps_used,
                    last_term_db,
                })),
            ) => {
                println!(
                    "  decision: BUDGET_EXHAUSTED steps_used={steps_used}/{limit} residual={last_term_db}"
                );
                assert_eq!(steps_used, limit, "[{id}] must stop exactly at budget");
            }
            (ExpectVerdict::ParseError, Err(DriverError::Parse { message, offset })) => {
                println!("  decision: PARSE_ERROR offset={offset}: {message}");
            }
            (expected, outcome) => panic!(
                "[{id}] verdict mismatch: expected {expected:?}, got {}",
                match &outcome {
                    Ok(r) => format!(
                        "Ok(steps={}, nf={})",
                        r.steps_used,
                        term_to_string(&r.normal_form)
                    ),
                    Err(e) => format!("Err({e:?})"),
                }
            ),
        }
    }
}
