//! Small-variable exhaustive acceptance tests.
//!
//! For every generated (A, B) pair the expected mathematical result is
//! computed independently in this test file via truth tables:
//! - if A & B is satisfiable, the service must reject jointly_satisfiable;
//! - if A & B is unsatisfiable, the returned interpolant must independently
//!   satisfy the three Craig conditions.
//!
//! Three structurally different families are covered explicitly:
//! 1. no common variables,
//! 2. antecedent itself contradictory,
//! 3. inputs jointly satisfiable.

use ipc_checker::verify_semantic;
use ipc_core::{EngineInput, EngineVerdict, InterpolationEngine, ResolutionBudget};
use ipc_service::ServiceConfig;
use ipc_service::{handle_interpolate, InterpolateRequest};
use ipc_syntax::{evaluate, parse, Formula};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Copy)]
struct TruthTable {
    /// Bits for assignments ordered false..true over one variable.
    bits: u8,
}

impl TruthTable {
    fn value(&self, assignment: bool) -> bool {
        let bit = if assignment { 1 } else { 0 };
        (self.bits >> bit) & 1 == 1
    }

    const fn all() -> [TruthTable; 4] {
        [
            TruthTable { bits: 0b00 }, // false
            TruthTable { bits: 0b01 }, // x
            TruthTable { bits: 0b10 }, // !x
            TruthTable { bits: 0b11 }, // true
        ]
    }

    fn formula(&self, variable: &str) -> Formula {
        match self.bits {
            0b00 => Formula::Const(false),
            0b01 => Formula::var(variable),
            0b10 => Formula::not(Formula::var(variable)),
            0b11 => Formula::Const(true),
            _ => unreachable!(),
        }
    }

    fn label(&self) -> &'static str {
        match self.bits {
            0b00 => "false",
            0b01 => "x",
            0b10 => "!x",
            _ => "true",
        }
    }
}

fn evaluate1(table: TruthTable, value: bool) -> bool {
    table.value(value)
}

/// Independent one-variable truth-table satisfiability of A & B.
fn conjunction_satisfiable1(a: TruthTable, b: TruthTable) -> Option<(bool, bool)> {
    for value in [false, true] {
        if evaluate1(a, value) && evaluate1(b, value) {
            return Some((value, value));
        }
    }
    None
}

fn run_engine(a: &Formula, b: &Formula, budget: Option<u64>) -> EngineVerdict {
    let mut engine = InterpolationEngine::new("exhaustive");
    engine.run(EngineInput {
        a,
        b,
        budget: budget
            .map(ResolutionBudget::capped)
            .unwrap_or_else(ResolutionBudget::unlimited),
    })
}

#[test]
fn enumerates_all_one_variable_pairs() {
    let mut proved_count = 0;
    let mut sat_count = 0;
    for a in TruthTable::all() {
        for b in TruthTable::all() {
            let formula_a = a.formula("x");
            let formula_b = b.formula("x");
            let independently_sat = conjunction_satisfiable1(a, b).is_some();
            let verdict = run_engine(&formula_a, &formula_b, None);
            match (independently_sat, verdict) {
                (true, EngineVerdict::JointlySatisfiable(_)) => sat_count += 1,
                (false, EngineVerdict::Proved(proved)) => {
                    let report = verify_semantic(&formula_a, &formula_b, &proved.interpolant);
                    assert!(
                        report.accepted(),
                        "A={} B={}: interpolant {:?} failed {:?}",
                        a.label(),
                        b.label(),
                        proved.interpolant,
                        report.failures
                    );
                    proved_count += 1;
                }
                (expected_sat, verdict) => panic!(
                    "A={} B={}: expected sat={expected_sat}, got {verdict:?}",
                    a.label(),
                    b.label()
                ),
            }
        }
    }
    assert_eq!(proved_count, 9, "exactly nine 1-var pairs are inconsistent");
    assert_eq!(sat_count, 7, "exactly seven 1-var pairs are jointly satisfiable");
}

#[test]
fn family_no_common_variables_covers_both_outcomes() {
    // A ranges over p, B ranges over the independent variable q.
    let mut no_common_unsat = 0;
    let mut no_common_sat = 0;
    for a in TruthTable::all() {
        for b in TruthTable::all() {
            let formula_a = a.formula("p");
            let formula_b = b.formula("q");
            let shared: BTreeSet<String> = formula_a
                .variables()
                .into_iter()
                .collect::<BTreeSet<_>>()
                .intersection(&formula_b.variables().into_iter().collect())
                .cloned()
                .collect();
            assert!(shared.is_empty(), "family must have no common variable");

            // Independent semantics over (p,q).
            let mut sat = false;
            for pa in [false, true] {
                for qb in [false, true] {
                    let mut map = BTreeMap::new();
                    map.insert("p".to_string(), pa);
                    map.insert("q".to_string(), qb);
                    if evaluate(&formula_a, &map) && evaluate(&formula_b, &map) {
                        sat = true;
                    }
                }
            }
            match run_engine(&formula_a, &formula_b, None) {
                EngineVerdict::JointlySatisfiable(_) => {
                    assert!(sat);
                    no_common_sat += 1;
                }
                EngineVerdict::Proved(proved) => {
                    assert!(!sat);
                    assert!(
                        proved.interpolant.variables().is_empty(),
                        "disjoint-alphabet interpolant must be constant"
                    );
                    let report = verify_semantic(&formula_a, &formula_b, &proved.interpolant);
                    assert!(report.accepted(), "{:?}", report.failures);
                    no_common_unsat += 1;
                }
                EngineVerdict::Unknown(reason) => panic!("unexpected unknown {reason:?}"),
            }
        }
    }
    assert_eq!(no_common_unsat, 7, "disjoint pairs where one side is false");
    assert_eq!(no_common_sat, 9);
}

#[test]
fn family_antecedent_contradiction_always_gives_false() {
    // A is a literal contradiction regardless of an unrelated structure.
    let contradiction_forms = ["p & !p", "q & !q", "(r | s) & !r & !s"];
    for a_text in contradiction_forms {
        let a = parse(a_text).unwrap();
        for b_text in ["z", "!z", "z & w", "true"] {
            let b = parse(b_text).unwrap();
            match run_engine(&a, &b, None) {
                EngineVerdict::Proved(proved) => {
                    assert_eq!(
                        proved.interpolant,
                        Formula::Const(false),
                        "A={a_text} B={b_text}"
                    );
                    let report = verify_semantic(&a, &b, &proved.interpolant);
                    assert!(report.accepted(), "{:?}", report.failures);
                }
                other => panic!("A contradiction must prove false, got {other:?}"),
            }
        }
    }
}

#[test]
fn family_jointly_satisfiable_never_emits_interpolant() {
    let pairs = [
        ("p", "p"),
        ("p & q", "q"),
        ("p | q", "p"),
        ("a -> b", "b"),
        ("a & b & c", "a | d"),
    ];
    for (a_text, b_text) in pairs {
        let a = parse(a_text).unwrap();
        let b = parse(b_text).unwrap();
        match run_engine(&a, &b, None) {
            EngineVerdict::JointlySatisfiable(witness) => {
                assert!(evaluate(&a, &witness.assignment));
                assert!(evaluate(&b, &witness.assignment));
            }
            other => panic!("{a_text},{b_text} must be sat, got {other:?}"),
        }
    }
}

#[test]
fn unknown_budget_never_carries_a_claimed_interpolant_through_service() {
    let config = ServiceConfig::default();
    let request = InterpolateRequest {
        request_id: "ex-budget".into(),
        antecedent: "a & b & c & d".into(),
        consequent: "!a | !b | !c | !d".into(),
        budget: Some(0),
        include_proof: Some(true),
    };
    let response = handle_interpolate(request, &config);
    match response.status {
        ipc_service::InterpolateStatus::Unknown { reason, detail } => {
            assert_eq!(reason, "budget_exhausted");
            assert!(detail["note"]
                .as_str()
                .unwrap()
                .contains("no proven interpolant"));
        }
        other => panic!("expected unknown, got {other:?}"),
    }
}
