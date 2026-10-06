//! Independent streaming checker for resolution proofs.
//!
//! The checker consumes a proof step stream and maintains only the clauses
//! currently referenced by the proof (a `d` record frees one). Every
//! resolution step is re-derived independently and compared against the
//! claimed resolvent. Resource limits are enforced: exceeding them yields
//! [`Verdict::Unverified`], never [`Verdict::Verified`].

use crate::proof::{ProofStep, StepReader};
use crate::resolve::{resolve, ResolveError};
use crate::syntax::{Clause, SyntaxError};
use crate::CHECKER_VERSION;
use serde::Serialize;
use std::collections::{HashMap, HashSet};
use std::io::BufRead;

/// Resource limits. Any exceeded limit aborts the check as `Unverified`.
#[derive(Clone, Copy, Debug)]
pub struct CheckerLimits {
    /// Maximum number of proof records processed.
    pub max_steps: usize,
    /// Maximum literals in any single stored clause.
    pub max_clause_lits: usize,
    /// Maximum total literals across all live stored clauses.
    pub max_total_lits: usize,
}

impl Default for CheckerLimits {
    fn default() -> Self {
        CheckerLimits {
            max_steps: 1_000_000,
            max_clause_lits: 1_000_000,
            max_total_lits: 16_000_000,
        }
    }
}

/// Final verdict of a check run.
#[derive(Clone, Copy, PartialEq, Eq, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Verdict {
    /// The proof is valid and derives the empty clause.
    Verified,
    /// The proof is invalid; `failure` names the concrete reason.
    Rejected,
    /// The checker could not reach a conclusion (resource exhaustion, or
    /// the stream ended before the empty clause was derived).
    Unverified,
}

/// Machine-readable classification of why a check did not verify.
#[derive(Clone, PartialEq, Eq, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FailureClass {
    /// A proof line could not be parsed.
    ParseError,
    /// Two records reuse the same id.
    DuplicateStepId,
    /// A declared clause repeats a variable.
    DuplicateLiteral,
    /// A parent id was never introduced.
    DanglingParent,
    /// A parent id refers to a clause removed by a `d` record.
    DeletedParent,
    /// The pivot does not occur with opposite signs in the parents.
    PivotMissing,
    /// The claimed resolvent still contains the pivot variable.
    PivotNotEliminated,
    /// Resolving on the pivot leaves a tautological clause.
    TautologicalResolvent,
    /// The claimed resolvent differs from the independently computed one.
    ClauseMismatch,
    /// A configured resource limit was exceeded (verdict: unverified).
    ResourceExhausted,
    /// The stream ended without deriving the empty clause
    /// (verdict: unverified).
    EmptyClauseNotDerived,
}

/// Concrete failure (or uncertainty) attached to a report.
#[derive(Clone, PartialEq, Eq, Debug, Serialize)]
pub struct FailureInfo {
    pub class: FailureClass,
    /// Proof step id involved, when applicable.
    pub step: Option<u64>,
    /// Input line where the problem was detected.
    pub line: usize,
    pub message: String,
}

/// One human-readable log entry emitted while checking.
#[derive(Clone, PartialEq, Eq, Debug, Serialize)]
pub struct LogEntry {
    pub level: &'static str,
    pub step: Option<u64>,
    pub line: usize,
    pub message: String,
}

/// Full, self-describing result of one check run.
#[derive(Clone, Debug, Serialize)]
pub struct CheckReport {
    /// Caller-supplied identity tying this report to a request.
    pub request_id: String,
    pub checker_version: String,
    pub steps_processed: usize,
    pub verdict: Verdict,
    /// Definitive reason for `rejected`; `None` otherwise.
    pub failure: Option<FailureInfo>,
    /// Reasons the checker stayed inconclusive; kept separate from
    /// definitive failures so callers never confuse the two.
    pub uncertainties: Vec<String>,
    pub log: Vec<LogEntry>,
}

impl CheckReport {
    pub fn to_json(&self) -> String {
        serde_json::to_string_pretty(self).expect("report serialization is infallible")
    }
}

struct State {
    clauses: HashMap<u64, Clause>,
    deleted: HashSet<u64>,
    total_lits: usize,
    steps: usize,
    line: usize,
}

/// The streaming checker. Construct with limits and a request identity,
/// then run [`Checker::check_stream`].
pub struct Checker {
    limits: CheckerLimits,
    request_id: String,
}

impl Checker {
    pub fn new(limits: CheckerLimits, request_id: impl Into<String>) -> Self {
        Checker {
            limits,
            request_id: request_id.into(),
        }
    }

    /// Check a proof stream, record by record. Stops early on the first
    /// definitive failure or as soon as the empty clause is derived.
    pub fn check_stream<R: BufRead>(&self, reader: R) -> CheckReport {
        let mut report = CheckReport {
            request_id: self.request_id.clone(),
            checker_version: CHECKER_VERSION.to_string(),
            steps_processed: 0,
            verdict: Verdict::Unverified,
            failure: None,
            uncertainties: Vec::new(),
            log: Vec::new(),
        };
        let mut state = State {
            clauses: HashMap::new(),
            deleted: HashSet::new(),
            total_lits: 0,
            steps: 0,
            line: 0,
        };
        info(
            &mut report,
            None,
            0,
            format!(
                "request {}: checker v{} started (limits: steps<={}, clause_lits<={}, total_lits<={})",
                self.request_id,
                CHECKER_VERSION,
                self.limits.max_steps,
                self.limits.max_clause_lits,
                self.limits.max_total_lits
            ),
        );

        for item in StepReader::new(reader) {
            let step = match item {
                Ok(step) => step,
                Err(e) => {
                    state.line = e.line;
                    return reject(
                        report,
                        state,
                        FailureClass::ParseError,
                        None,
                        e.message,
                    );
                }
            };
            state.line += 1; // logical record index used as position hint
            state.steps += 1;
            if state.steps > self.limits.max_steps {
                let steps = state.steps;
                return unverified(
                    report,
                    state,
                    format!(
                        "step limit {} exceeded at record {}",
                        self.limits.max_steps, steps
                    ),
                );
            }
            report.steps_processed = state.steps;

            let outcome = match &step {
                ProofStep::Axiom { id, lits } => self.add_clause(&mut state, *id, lits.clone()),
                ProofStep::Resolve {
                    id,
                    left,
                    right,
                    pivot,
                    claimed,
                } => self.check_resolution(&mut state, *id, *left, *right, *pivot, claimed),
                ProofStep::Delete { id } => self.delete_clause(&mut state, *id),
            };

            match outcome {
                Ok(derived_empty) => {
                    info(
                        &mut report,
                        Some(step.id()),
                        state.line,
                        describe(&step, &state),
                    );
                    if derived_empty {
                        info(
                            &mut report,
                            Some(step.id()),
                            state.line,
                            "empty clause derived; proof verified".to_string(),
                        );
                        report.verdict = Verdict::Verified;
                        return report;
                    }
                }
                Err((class, message)) => {
                    if class == FailureClass::ResourceExhausted {
                        // Resource exhaustion is never a definitive verdict.
                        return unverified(report, state, message);
                    }
                    return reject(report, state, class, Some(step.id()), message);
                }
            }
        }

        unverified(
            report,
            state,
            "proof stream ended without deriving the empty clause".to_string(),
        )
    }

    fn add_clause(
        &self,
        state: &mut State,
        id: u64,
        lits: Vec<crate::syntax::Literal>,
    ) -> StepOutcome {
        if state.clauses.contains_key(&id) || state.deleted.contains(&id) {
            return Err((
                FailureClass::DuplicateStepId,
                format!("clause id {id} is already in use"),
            ));
        }
        let clause = Clause::from_raw(lits).map_err(|e: SyntaxError| {
            (
                FailureClass::DuplicateLiteral,
                format!("clause {id}: {e}"),
            )
        })?;
        self.enforce_size_limits(state, clause.len())?;
        state.total_lits += clause.len();
        state.clauses.insert(id, clause);
        Ok(false)
    }

    fn check_resolution(
        &self,
        state: &mut State,
        id: u64,
        left: u64,
        right: u64,
        pivot: u32,
        claimed: &[crate::syntax::Literal],
    ) -> StepOutcome {
        if state.clauses.contains_key(&id) || state.deleted.contains(&id) {
            return Err((
                FailureClass::DuplicateStepId,
                format!("clause id {id} is already in use"),
            ));
        }
        let parent = |pid: u64| -> Result<Clause, (FailureClass, String)> {
            if let Some(c) = state.clauses.get(&pid) {
                Ok(c.clone())
            } else if state.deleted.contains(&pid) {
                Err((
                    FailureClass::DeletedParent,
                    format!("step {id}: parent {pid} was deleted before this step"),
                ))
            } else {
                Err((
                    FailureClass::DanglingParent,
                    format!("step {id}: parent {pid} does not exist"),
                ))
            }
        };
        let left_clause = parent(left)?;
        let right_clause = parent(right)?;

        let resolvent = resolve(&left_clause, &right_clause, pivot).map_err(|e| match e {
            ResolveError::PivotMissing { .. } => (FailureClass::PivotMissing, e.to_string()),
            ResolveError::TautologicalResolvent { .. } => {
                (FailureClass::TautologicalResolvent, e.to_string())
            }
        })?;

        let claimed_clause = Clause::from_raw(claimed.to_vec()).map_err(|e: SyntaxError| {
            (
                FailureClass::DuplicateLiteral,
                format!("step {id}: claimed resolvent: {e}"),
            )
        })?;

        if claimed_clause.contains_var(pivot) {
            return Err((
                FailureClass::PivotNotEliminated,
                format!("step {id}: claimed resolvent still contains pivot variable {pivot}"),
            ));
        }
        if claimed_clause != resolvent {
            return Err((
                FailureClass::ClauseMismatch,
                format!(
                    "step {id}: claimed resolvent {} but resolution on pivot {pivot} yields {}",
                    claimed_clause, resolvent
                ),
            ));
        }

        let derived_empty = resolvent.is_empty();
        self.enforce_size_limits(state, resolvent.len())?;
        state.total_lits += resolvent.len();
        state.clauses.insert(id, resolvent);
        Ok(derived_empty)
    }

    fn delete_clause(&self, state: &mut State, id: u64) -> StepOutcome {
        match state.clauses.remove(&id) {
            Some(clause) => {
                state.total_lits -= clause.len();
                state.deleted.insert(id);
                Ok(false)
            }
            None if state.deleted.contains(&id) => Err((
                FailureClass::DeletedParent,
                format!("clause {id} was already deleted"),
            )),
            None => Err((
                FailureClass::DanglingParent,
                format!("cannot delete clause {id}: it does not exist"),
            )),
        }
    }

    fn enforce_size_limits(&self, state: &State, clause_len: usize) -> StepOutcome {
        if clause_len > self.limits.max_clause_lits {
            return Err((
                FailureClass::ResourceExhausted,
                format!(
                    "clause size {clause_len} exceeds limit {}",
                    self.limits.max_clause_lits
                ),
            ));
        }
        if state.total_lits + clause_len > self.limits.max_total_lits {
            return Err((
                FailureClass::ResourceExhausted,
                format!(
                    "total stored literals would exceed limit {}",
                    self.limits.max_total_lits
                ),
            ));
        }
        Ok(false)
    }
}

/// `Ok(true)` means the empty clause was derived.
type StepOutcome = Result<bool, (FailureClass, String)>;

fn info(report: &mut CheckReport, step: Option<u64>, line: usize, message: String) {
    report.log.push(LogEntry {
        level: "INFO",
        step,
        line,
        message,
    });
}

fn describe(step: &ProofStep, state: &State) -> String {
    match step {
        ProofStep::Axiom { id, .. } => {
            format!("axiom {id} stored ({} live clauses)", state.clauses.len())
        }
        ProofStep::Resolve {
            id, left, right, pivot, ..
        } => format!("step {id}: resolved {left} x {right} on pivot {pivot}; resolvent verified"),
        ProofStep::Delete { id } => {
            format!("clause {id} deleted ({} live clauses)", state.clauses.len())
        }
    }
}

fn reject(
    mut report: CheckReport,
    state: State,
    class: FailureClass,
    step: Option<u64>,
    message: String,
) -> CheckReport {
    report.verdict = Verdict::Rejected;
    report.log.push(LogEntry {
        level: "ERROR",
        step,
        line: state.line,
        message: message.clone(),
    });
    report.failure = Some(FailureInfo {
        class,
        step,
        line: state.line,
        message,
    });
    report
}

fn unverified(mut report: CheckReport, state: State, reason: String) -> CheckReport {
    report.verdict = Verdict::Unverified;
    report.log.push(LogEntry {
        level: "WARN",
        step: None,
        line: state.line,
        message: reason.clone(),
    });
    report.uncertainties.push(reason);
    report
}
