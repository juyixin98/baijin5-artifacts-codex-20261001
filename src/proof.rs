//! Proof records: run id, ordered step log and reusable logger trait.
//!
//! Every notable intermediate state of expansion and evaluation is appended as
//! a [`Step`]. The independent checker replays the expansion budget from these
//! steps and cross-checks semantics with its own evaluator.

use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

use crate::syntax::Formula;

/// Ternary verdict used whenever residual quantifiers may remain.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Verdict {
    True,
    False,
    /// Expansion could not finish; the formula still contains quantifiers.
    Unknown,
}

impl Verdict {
    pub fn as_str(self) -> &'static str {
        match self {
            Verdict::True => "true",
            Verdict::False => "false",
            Verdict::Unknown => "unknown",
        }
    }
}

/// One replayable event in the proof trace.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "event", rename_all = "snake_case")]
pub enum Step {
    /// Expansion began on a quantifier with domain size `size`.
    ExpandStart {
        seq: u64,
        quantifier: String,
        var: String,
        sort: String,
        size: usize,
    },
    /// A single ground instance was produced (one unit of instantiation budget).
    Instantiate {
        seq: u64,
        quantifier: String,
        var: String,
        value: String,
        used: u64,
        limit: u64,
    },
    /// The quantifier fully expanded.
    ExpandDone {
        seq: u64,
        quantifier: String,
        instances: u64,
        remaining_budget: u64,
    },
    /// A parent quantifier rolled back already-paid instances because a nested
    /// quantifier exhausted the budget and the parent node was preserved.
    RollbackInstances {
        seq: u64,
        quantifier: String,
        rolled_back: u64,
        used_after: u64,
    },
    /// Budget hit zero while expanding a quantifier; node was left intact.
    BudgetExhausted {
        seq: u64,
        quantifier: String,
        var: String,
        sort: String,
        used: u64,
        limit: u64,
    },
    /// A ground term evaluated to an element.
    EvalTerm {
        seq: u64,
        term: String,
        value: String,
    },
    /// A ground atom evaluated to a boolean.
    EvalAtom { seq: u64, atom: String, value: bool },
    /// Evaluating the whole (possibly residual) formula produced a verdict.
    EvalFormula {
        seq: u64,
        verdict: Verdict,
        reason: String,
    },
    /// Free-form human note (also machine retained in the JSON record).
    Note { seq: u64, text: String },
}

impl Step {
    pub fn seq(&self) -> u64 {
        match self {
            Step::ExpandStart { seq, .. }
            | Step::Instantiate { seq, .. }
            | Step::RollbackInstances { seq, .. }
            | Step::ExpandDone { seq, .. }
            | Step::BudgetExhausted { seq, .. }
            | Step::EvalTerm { seq, .. }
            | Step::EvalAtom { seq, .. }
            | Step::EvalFormula { seq, .. }
            | Step::Note { seq, .. } => *seq,
        }
    }
}

/// Persisted proof record.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProofRecord {
    pub run_id: String,
    pub created_unix_nanos: u128,
    pub model_name: String,
    pub original_formula: Formula,
    pub allow_empty_domain: bool,
    pub instantiation_budget: u64,
    pub instantiations_used: u64,
    pub node_cap: u64,
    pub expanded_formula: Formula,
    pub residual_quantifiers: usize,
    pub verdict: Verdict,
    pub reason: String,
    #[serde(default)]
    pub steps: Vec<Step>,
}

/// Sink for diagnostic/proof events.
pub trait RunLogger {
    fn emit(&mut self, step: Step);

    fn note(&mut self, text: String) {
        self.emit(Step::Note { seq: 0, text });
    }
}

/// Collects steps in memory; the production logger.
#[derive(Debug, Default)]
pub struct VecLogger {
    pub steps: Vec<Step>,
}

impl VecLogger {
    pub fn new() -> Self {
        Self { steps: Vec::new() }
    }
}

impl RunLogger for VecLogger {
    fn emit(&mut self, mut step: Step) {
        if step.seq() == 0 {
            step = with_seq(step, self.steps.len() as u64 + 1);
        }
        self.steps.push(step);
    }
}

/// Discards events; used by the checker's internal evaluator.
pub struct NullLogger;

impl RunLogger for NullLogger {
    fn emit(&mut self, _step: Step) {}
}

fn with_seq(step: Step, seq: u64) -> Step {
    match step {
        Step::ExpandStart {
            seq: _,
            quantifier,
            var,
            sort,
            size,
        } => Step::ExpandStart {
            seq,
            quantifier,
            var,
            sort,
            size,
        },
        Step::Instantiate {
            seq: _,
            quantifier,
            var,
            value,
            used,
            limit,
        } => Step::Instantiate {
            seq,
            quantifier,
            var,
            value,
            used,
            limit,
        },
        Step::RollbackInstances {
            seq: _,
            quantifier,
            rolled_back,
            used_after,
        } => Step::RollbackInstances {
            seq,
            quantifier,
            rolled_back,
            used_after,
        },
        Step::ExpandDone {
            seq: _,
            quantifier,
            instances,
            remaining_budget,
        } => Step::ExpandDone {
            seq,
            quantifier,
            instances,
            remaining_budget,
        },
        Step::BudgetExhausted {
            seq: _,
            quantifier,
            var,
            sort,
            used,
            limit,
        } => Step::BudgetExhausted {
            seq,
            quantifier,
            var,
            sort,
            used,
            limit,
        },
        Step::EvalTerm {
            seq: _,
            term,
            value,
        } => Step::EvalTerm { seq, term, value },
        Step::EvalAtom {
            seq: _,
            atom,
            value,
        } => Step::EvalAtom { seq, atom, value },
        Step::EvalFormula {
            seq: _,
            verdict,
            reason,
        } => Step::EvalFormula {
            seq,
            verdict,
            reason,
        },
        Step::Note { seq: _, text } => Step::Note { seq, text },
    }
}

static RUN_COUNTER: AtomicU64 = AtomicU64::new(0);

/// Generate a replayable run id: unix millis plus a process local counter.
pub fn new_run_id() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let counter = RUN_COUNTER.fetch_add(1, Ordering::Relaxed);
    format!("run-{:013}-{:04}", nanos / 1_000_000, counter)
}

pub fn now_nanos() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0)
}
