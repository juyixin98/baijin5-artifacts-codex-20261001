//! Proof records: an auditable trace of a counting run.
//!
//! Every node evaluation is recorded together with its scope, the raw
//! count over that scope, and the smoothing factor (2^k) applied by its
//! parent for variables that do not occur in the subcircuit. The record
//! is JSON-serializable so an independent party can replay or inspect
//! why a count is what it is. Big integers are stored as decimal
//! strings to avoid precision loss in JSON tooling.

use crate::syntax::{NodeId, Var};
use serde::{Deserialize, Serialize};

/// How the determinism of one OR-child pair was established.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "method", rename_all = "snake_case")]
pub enum DeterminismEvidence {
    /// A child is unsatisfiable, so the pair is trivially disjoint.
    UnsatChild { child: NodeId },
    /// Both children force opposite values of the same variable.
    ForcedLiteral { var: Var },
    /// Exhaustive enumeration over the (small) joined scope found no
    /// assignment satisfying both children.
    Enumeration { assignments_checked: u64 },
}

/// Record of one validated AND node.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AndCheckRecord {
    pub node: NodeId,
    /// Per-child scopes; pairwise disjointness was verified.
    pub child_scopes: Vec<Vec<Var>>,
}

/// Record of one validated OR child pair.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OrCheckRecord {
    pub node: NodeId,
    pub pair: (NodeId, NodeId),
    pub evidence: DeterminismEvidence,
}

/// Validation part of the proof: why the circuit was accepted as
/// decomposable and deterministic.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ValidationProof {
    pub and_checks: Vec<AndCheckRecord>,
    pub or_checks: Vec<OrCheckRecord>,
}

/// One node evaluation in the counting sweep.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CountEntry {
    pub node: NodeId,
    pub kind: String,
    /// Scope of the subcircuit (sorted variable list).
    pub scope: Vec<Var>,
    /// Model count of the subcircuit over its own scope, decimal string.
    pub raw_count: String,
    /// Number of parent-scope variables missing below this node; the
    /// parent multiplies this node's contribution by 2^smooth_exp.
    pub smooth_exp: u32,
}

/// Full counting proof for one request.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProofRecord {
    pub request_id: String,
    pub root: NodeId,
    /// Declared variable universe the final count ranges over.
    pub declared: Vec<Var>,
    pub validation: ValidationProof,
    pub entries: Vec<CountEntry>,
    /// Smoothing exponent applied at the top level.
    pub top_smooth_exp: u32,
    /// Final model count over `declared`, decimal string.
    pub total: String,
}

impl ProofRecord {
    pub fn to_json(&self) -> String {
        serde_json::to_string_pretty(self).expect("proof serialization cannot fail")
    }
}
