//! Proof records: run identifiers and structured elimination traces.
//!
//! Every elimination run gets a [`new_run_id`] at start; each recorded step
//! carries that run id, a monotonically increasing sequence number, the
//! recursion depth, the action taken, and a human-readable reason. The
//! trace is serialized into reports and log files so a run can be replayed
//! and audited after the fact.

use serde::{Deserialize, Serialize};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

static RUN_COUNTER: AtomicU64 = AtomicU64::new(0);

/// Generate a process-unique run identifier: `run-<unix_millis>-<seq>`.
pub fn new_run_id() -> String {
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    let seq = RUN_COUNTER.fetch_add(1, Ordering::Relaxed);
    format!("run-{millis}-{seq:04}")
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum QuantKind {
    Forall,
    Exists,
}

impl QuantKind {
    pub fn symbol(self) -> &'static str {
        match self {
            QuantKind::Forall => "forall",
            QuantKind::Exists => "exists",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "action", rename_all = "snake_case")]
pub enum Action {
    /// A quantifier was expanded over the whole domain.
    Expand {
        kind: QuantKind,
        var: String,
        domain_size: usize,
        nodes_after: usize,
    },
    /// A quantifier over an empty domain was replaced by the vacuous
    /// constant (true for forall, false for exists).
    EmptyDomainExpansion { kind: QuantKind, var: String },
    /// The expansion budget ran out; the quantifier was kept unexpanded and
    /// the overall result is marked unknown.
    BudgetExhausted { kind: QuantKind, var: String },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProofStep {
    pub run_id: String,
    pub seq: u64,
    pub depth: u32,
    #[serde(flatten)]
    pub action: Action,
    /// Judgment reason: why this step was taken, in replayable terms.
    pub reason: String,
}
