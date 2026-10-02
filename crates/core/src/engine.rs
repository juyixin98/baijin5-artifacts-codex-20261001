//! Budgeted ordered-resolution engine producing an unsatisfiability proof.
//!
//! Algorithm: Davis-Putnam variable elimination. Variables are removed in
//! deterministic sorted order. For each variable the positive-literal clauses
//! are resolved against the negative-literal clauses; every resolvent is
//! recorded in a proof DAG so an independent checker can replay the proof.
//!
//! The engine distinguishes exactly three outcomes:
//! - `Proved`: empty clause derived, interpolant built;
//! - `JointlySatisfiable`: elimination finished without the empty clause;
//! - `Unknown`: the resolution budget ran out before deciding.

use crate::budget::{BudgetCounter, Charge, ResolutionBudget};
use crate::cnf::{to_cnf_direct, to_cnf_tseitin, TseitinEncoding};
use crate::events::EventLog;
use crate::interpolant::{build_annotations, extract_interpolant};
use crate::witness::find_model;
use ipc_proof::{Clause, Proof, ProofBuilder, Side};
use ipc_syntax::Formula;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum UnknownReason {
    BudgetExhausted { used: u64, cap: u64 },
}

#[derive(Debug, Clone)]
pub struct Proved {
    pub interpolant: Formula,
    pub proof: Proof,
    pub resolutions_used: u64,
    pub variables_eliminated: u32,
}

#[derive(Debug, Clone)]
pub struct JointlySatisfiable {
    pub assignment: BTreeMap<String, bool>,
}

#[derive(Debug, Clone)]
pub enum EngineVerdict {
    Proved(Proved),
    JointlySatisfiable(JointlySatisfiable),
    Unknown(UnknownReason),
}

#[derive(Debug, Clone)]
pub struct EncodedProblem {
    pub a_clauses: Vec<Clause>,
    pub b_clauses: Vec<Clause>,
    pub a_original_vars: BTreeSet<String>,
    pub b_original_vars: BTreeSet<String>,
    pub all_vars: Vec<String>,
}

#[derive(Debug, Clone, Copy)]
pub struct EngineInput<'a> {
    pub a: &'a Formula,
    pub b: &'a Formula,
    pub budget: ResolutionBudget,
}

#[derive(Debug, Clone)]
struct ActiveClause {
    clause: Clause,
    node: usize,
}

enum EliminationResult {
    EmptyClause(usize),
    NoEmptyClause,
    BudgetExhausted,
}

pub struct InterpolationEngine {
    pub log: EventLog,
    builder: ProofBuilder,
    counter: BudgetCounter,
    variables_eliminated: u32,
}

impl InterpolationEngine {
    pub fn new(request_id: impl Into<String>) -> Self {
        InterpolationEngine {
            log: EventLog::new(request_id),
            builder: ProofBuilder::new(),
            counter: BudgetCounter::new(),
            variables_eliminated: 0,
        }
    }

    pub fn run(&mut self, input: EngineInput<'_>) -> EngineVerdict {
        let problem = self.encode(input.a, input.b);
        match self.eliminate(&problem, input.budget) {
            EliminationResult::EmptyClause(root) => {
                let proof = self.builder.clone_nodes();
                let proof = Proof::new(proof, root);
                proof.validate().expect("engine emits valid proof");
                let annotations = build_annotations(
                    &proof,
                    &problem.a_original_vars,
                    &problem.b_original_vars,
                )
                .expect("valid proof annotates");
                let interpolant =
                    extract_interpolant(&proof, &annotations).expect("root annotation exists");
                self.log.record(
                    "core::interpolant",
                    format!("root interpolant is '{}'", interpolant.to_pretty()),
                );
                EngineVerdict::Proved(Proved {
                    interpolant,
                    proof,
                    resolutions_used: self.counter.used(),
                    variables_eliminated: self.variables_eliminated,
                })
            }
            EliminationResult::BudgetExhausted => {
                let cap = input.budget.max_resolutions.unwrap_or(0);
                self.log.warn(
                    "core::budget",
                    format!(
                        "resolution budget exhausted after {} of at most {} steps; \
                         satisfiability is unknown and no interpolant is claimed",
                        self.counter.used(),
                        cap
                    ),
                );
                EngineVerdict::Unknown(UnknownReason::BudgetExhausted {
                    used: self.counter.used(),
                    cap,
                })
            }
            EliminationResult::NoEmptyClause => {
                let all_clauses: Vec<Clause> = problem
                    .a_clauses
                    .iter()
                    .chain(problem.b_clauses.iter())
                    .cloned()
                    .collect();
                let assignment = find_model(&all_clauses, &problem.all_vars)
                    .expect("DP elimination claims sat, witness exists");
                self.log.record(
                    "core::solver",
                    "no empty clause derived; original conjunction has a model",
                );
                EngineVerdict::JointlySatisfiable(JointlySatisfiable { assignment })
            }
        }
    }

    fn encode(&mut self, a: &Formula, b: &Formula) -> EncodedProblem {
        let a_original_vars: BTreeSet<String> = a.variables().into_iter().collect();
        let b_original_vars: BTreeSet<String> = b.variables().into_iter().collect();

        let a_clauses = self.encode_side(a, "A");
        let b_clauses = self.encode_side(b, "B");

        let mut vars: BTreeSet<String> = BTreeSet::new();
        for clause in a_clauses.iter().chain(b_clauses.iter()) {
            for literal in &clause.literals {
                if literal.variable != "__const_true" {
                    vars.insert(literal.variable.clone());
                }
            }
        }
        self.log.record(
            "core::cnf",
            format!(
                "encoded side A into {} clauses and side B into {} clauses ({} vars)",
                a_clauses.len(),
                b_clauses.len(),
                vars.len()
            ),
        );
        EncodedProblem {
            a_clauses,
            b_clauses,
            a_original_vars,
            b_original_vars,
            all_vars: vars.into_iter().collect(),
        }
    }

    fn encode_side(&mut self, formula: &Formula, tag: &str) -> Vec<Clause> {
        if let Some(cnf) = to_cnf_direct(formula) {
            self.log
                .record("core::cnf", format!("{tag} used direct CNF path"));
            return cnf.clauses;
        }
        let encoding: TseitinEncoding = to_cnf_tseitin(formula, tag);
        self.log.record(
            "core::cnf",
            format!(
                "{tag} used Tseitin path with {} auxiliary variables",
                encoding.auxiliary.len()
            ),
        );
        encoding.clauses
    }

    fn eliminate(
        &mut self,
        problem: &EncodedProblem,
        budget: ResolutionBudget,
    ) -> EliminationResult {
        let mut active: Vec<ActiveClause> = Vec::new();
        for (side_clauses, side) in [
            (&problem.a_clauses, Side::A),
            (&problem.b_clauses, Side::B),
        ] {
            for (index, clause) in side_clauses.iter().enumerate() {
                let origin = format!("{:?}:{index}", side);
                let node = self.builder.add_hypothesis(side, clause.clone(), origin);
                if clause.is_empty() {
                    self.log.record(
                        "core::solver",
                        "input side already contains the empty clause",
                    );
                    return EliminationResult::EmptyClause(node);
                }
                active.push(ActiveClause {
                    clause: clause.clone(),
                    node,
                });
            }
        }

        for variable in &problem.all_vars {
            self.variables_eliminated += 1;
            let pos_indices: Vec<usize> = indices_with_polarity(&active, variable, true);
            let neg_indices: Vec<usize> = indices_with_polarity(&active, variable, false);

            if pos_indices.is_empty() || neg_indices.is_empty() {
                continue;
            }

            let mut resolvents: Vec<ActiveClause> = Vec::new();
            let mut empty = None;
            'outer: for &i in &pos_indices {
                for &j in &neg_indices {
                    let resolvent = match active[i].clause.resolve(&active[j].clause, variable)
                    {
                        Some(clause) => clause,
                        None => continue,
                    };
                    if matches!(self.counter.charge(budget), Charge::Exhausted { .. }) {
                        return EliminationResult::BudgetExhausted;
                    }
                    let node = self.builder.add_resolution(
                        variable.clone(),
                        active[i].node,
                        active[j].node,
                        resolvent.clone(),
                    );
                    if resolvent.is_empty() {
                        empty = Some(node);
                        break 'outer;
                    }
                    if active.iter().chain(resolvents.iter()).any(|entry| entry.clause == resolvent)
                    {
                        continue;
                    }
                    resolvents.push(ActiveClause {
                        clause: resolvent,
                        node,
                    });
                }
            }

            if let Some(root) = empty {
                self.log.record(
                    "core::solver",
                    format!("derived empty clause eliminating {variable}"),
                );
                return EliminationResult::EmptyClause(root);
            }

            let drop: BTreeSet<usize> = pos_indices
                .into_iter()
                .chain(neg_indices.into_iter())
                .collect();
            let mut next: Vec<ActiveClause> = Vec::new();
            for (index, entry) in active.into_iter().enumerate() {
                if !drop.contains(&index) {
                    next.push(entry);
                }
            }
            next.extend(resolvents);
            active = next;
        }

        EliminationResult::NoEmptyClause
    }
}

fn indices_with_polarity(active: &[ActiveClause], variable: &str, positive: bool) -> Vec<usize> {
    active
        .iter()
        .enumerate()
        .filter(|(_, entry)| {
            entry.clause.literals.iter().any(|literal| {
                literal.variable == variable && literal.positive == positive
            })
        })
        .map(|(index, _)| index)
        .collect()
}
