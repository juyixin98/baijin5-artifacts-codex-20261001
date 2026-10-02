use ipc_core::{
    EngineInput, EngineVerdict, InterpolationEngine, ResolutionBudget, UnknownReason,
};
use ipc_syntax::{parse, Formula};

fn run_pair(a: &str, b: &str, cap: Option<u64>) -> EngineVerdict {
    let a = parse(a).unwrap();
    let b = parse(b).unwrap();
    let mut engine = InterpolationEngine::new("core-e2e");
    engine.run(EngineInput {
        a: &a,
        b: &b,
        budget: match cap {
            Some(cap) => ResolutionBudget::capped(cap),
            None => ResolutionBudget::unlimited(),
        },
    })
}

#[test]
fn proves_shared_variable_case() {
    match run_pair("p", "!p", None) {
        EngineVerdict::Proved(proved) => {
            assert_eq!(proved.interpolant, Formula::var("p"));
            assert!(proved.proof.validate().is_ok());
        }
        other => panic!("expected proved, got {other:?}"),
    }
}

#[test]
fn proves_chain_deduction() {
    match run_pair("a & (!a | d)", "!d", None) {
        EngineVerdict::Proved(proved) => {
            assert_eq!(proved.interpolant, Formula::var("d"));
            assert!(proved.proof.validate().is_ok());
        }
        other => panic!("expected proved, got {other:?}"),
    }
}

#[test]
fn detects_joint_satisfaction() {
    match run_pair("p & q", "p | q", None) {
        EngineVerdict::JointlySatisfiable(witness) => {
            assert_eq!(witness.assignment.get("p"), Some(&true));
        }
        other => panic!("expected jointly satisfiable, got {other:?}"),
    }
}

#[test]
fn zero_budget_is_unknown_and_emits_no_interpolant() {
    match run_pair("p", "!p", Some(0)) {
        EngineVerdict::Unknown(UnknownReason::BudgetExhausted { cap, used }) => {
            assert_eq!(cap, 0);
            assert_eq!(used, 0);
        }
        other => panic!("budget 0 must be unknown, got {other:?}"),
    }
}

#[test]
fn handles_non_cnf_formula_without_auxiliary_leak() {
    match run_pair("a & b", "!a | !b", None) {
        EngineVerdict::Proved(proved) => {
            let vars = proved.interpolant.variables();
            assert!(
                vars.iter().all(|name| !name.starts_with("__tse_")),
                "interpolant leaked Tseitin variables: {vars:?}"
            );
            assert!(proved.proof.validate().is_ok());
        }
        other => panic!("expected proved, got {other:?}"),
    }
}

#[test]
fn constant_true_and_false_pair_is_unsat() {
    // B side is the contradiction (false), so the Pudlak annotation yields
    // the vacuously-true interpolant, which still excludes B.
    match run_pair("true", "false", None) {
        EngineVerdict::Proved(proved) => {
            assert_eq!(proved.interpolant, Formula::Const(true));
            assert!(proved.proof.validate().is_ok());
        }
        other => panic!("true & false must be unsat, got {other:?}"),
    }
}

#[test]
fn no_common_variables_contradiction_gives_false() {
    // A unsat on its own, disjoint alphabet => interpolant false.
    match run_pair("p & !p", "q", None) {
        EngineVerdict::Proved(proved) => {
            assert_eq!(proved.interpolant, Formula::Const(false));
            assert!(proved.proof.validate().is_ok());
        }
        other => panic!("expected proved false, got {other:?}"),
    }
}
