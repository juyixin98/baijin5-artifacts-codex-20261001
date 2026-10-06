//! Reasoning core: exact model counting over a validated circuit.
//!
//! The count of a node is computed over its own scope. Smoothing
//! factors of 2^k (one per absent variable) are applied exactly where
//! they are semantically required:
//!
//! * OR children: each child is smoothed up to the OR's scope before
//!   summing, because sibling scopes may differ.
//! * AND children: never. Decomposability means the child scopes
//!   partition the AND's scope, so the raw counts multiply directly.
//! * Top level: the root is smoothed up to the declared variable
//!   universe, so variables never mentioned in the circuit still
//!   contribute a factor of 2 each.
//!
//! All arithmetic uses big integers.

use crate::proof::{CountEntry, ProofRecord, ValidationProof};
use crate::syntax::{Circuit, Node, NodeId, Var};
use num_bigint::BigUint;
use std::collections::{BTreeSet, HashMap};

/// Result of a successful counting run.
pub struct CountOutcome {
    pub total: BigUint,
    pub proof: ProofRecord,
}

/// 2^k as a big integer.
pub fn pow2(k: u32) -> BigUint {
    BigUint::from(1u64) << k
}

/// Count models of `circuit` over `declared` (the variable universe).
///
/// The circuit must already be validated (decomposable AND,
/// deterministic OR); this function does not re-check those
/// properties. `validation` is the proof fragment produced by the
/// validator and is embedded into the resulting record.
pub fn count_models(
    circuit: &Circuit,
    declared: &BTreeSet<Var>,
    request_id: &str,
    validation: ValidationProof,
) -> CountOutcome {
    let order = circuit.topo_order().expect("validated circuit is well-formed");
    let scopes: HashMap<NodeId, BTreeSet<Var>> = circuit.scopes(&order).into_iter().collect();

    let mut raw: HashMap<NodeId, BigUint> = HashMap::new();
    let mut entries: Vec<CountEntry> = Vec::new();
    // Smoothing exponent applied to each node by its parent (or top).
    let mut smooth_of: HashMap<NodeId, u32> = HashMap::new();

    for &id in &order {
        let node = circuit.node(id).expect("known node");
        let value = match node {
            Node::True { .. } => BigUint::from(1u64),
            Node::False { .. } => BigUint::from(0u64),
            Node::Lit { .. } => BigUint::from(1u64),
            Node::And { children, .. } => {
                let mut acc = BigUint::from(1u64);
                for &c in children {
                    // Decomposable AND: child scopes partition this
                    // node's scope, hence no smoothing factor here.
                    acc *= &raw[&c];
                }
                acc
            }
            Node::Or { children, .. } => {
                let mut acc = BigUint::from(0u64);
                for &c in children {
                    let missing = scopes[&id].len() - scopes[&c].len();
                    *smooth_of.entry(c).or_insert(0) += missing as u32;
                    acc += &raw[&c] * pow2(missing as u32);
                }
                acc
            }
        };
        raw.insert(id, value);
    }

    let root_scope = &scopes[&circuit.root];
    let top_smooth = (declared.len() - root_scope.len()) as u32;
    let total = &raw[&circuit.root] * pow2(top_smooth);
    let total_str = total.to_string();

    for &id in &order {
        let node = circuit.node(id).expect("known node");
        entries.push(CountEntry {
            node: id,
            kind: node.kind().to_string(),
            scope: scopes[&id].iter().copied().collect(),
            raw_count: raw[&id].to_string(),
            smooth_exp: smooth_of.get(&id).copied().unwrap_or(0),
        });
    }

    CountOutcome {
        total,
        proof: ProofRecord {
            request_id: request_id.to_string(),
            root: circuit.root,
            declared: declared.iter().copied().collect(),
            validation,
            entries,
            top_smooth_exp: top_smooth,
            total: total_str,
        },
    }
}
