//! Structural validation of a circuit, run before counting.
//!
//! Pass 1 verifies that every AND node is decomposable: its children's
//! variable scopes are pairwise disjoint.
//!
//! Pass 2 verifies that every OR node is deterministic: no assignment
//! satisfies two children at once. For each child pair the validator
//! first looks for cheap proof evidence (an unsatisfiable child, or
//! opposing forced literals derived bottom-up); failing that, and only
//! when the joined scope is small enough (configured threshold), it
//! falls back to exhaustive enumeration as an independent check. If
//! neither applies, the pair is reported as undecidable instead of
//! being silently trusted.

use crate::config::Config;
use crate::proof::{AndCheckRecord, DeterminismEvidence, OrCheckRecord, ValidationProof};
use crate::syntax::{Circuit, Node, NodeId, Var};
use std::collections::{BTreeSet, HashMap};

/// Why a circuit was rejected (or could not be decided).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Rejection {
    AndNotDecomposable {
        node: NodeId,
        var: Var,
        children: (NodeId, NodeId),
    },
    OrNotDeterministic {
        node: NodeId,
        pair: (NodeId, NodeId),
        witness: Vec<(Var, bool)>,
    },
    Undecidable {
        node: NodeId,
        pair: (NodeId, NodeId),
        scope_size: usize,
    },
}

/// Validate `circuit`; on success return the validation proof fragment.
pub fn validate(circuit: &Circuit, config: &Config) -> Result<ValidationProof, Rejection> {
    let order = circuit
        .topo_order()
        .expect("syntax checked before validation");
    let scopes: HashMap<NodeId, BTreeSet<Var>> = circuit.scopes(&order).into_iter().collect();

    // Pass 1: AND decomposability, for every AND node.
    let mut and_checks = Vec::new();
    for &id in &order {
        if let Node::And { children, .. } = circuit.node(id).expect("known node") {
            let mut seen: HashMap<Var, NodeId> = HashMap::new();
            for &c in children {
                for &v in &scopes[&c] {
                    if let Some(&other) = seen.get(&v) {
                        return Err(Rejection::AndNotDecomposable {
                            node: id,
                            var: v,
                            children: (other, c),
                        });
                    }
                    seen.insert(v, c);
                }
            }
            and_checks.push(AndCheckRecord {
                node: id,
                child_scopes: children
                    .iter()
                    .map(|c| scopes[c].iter().copied().collect())
                    .collect(),
            });
        }
    }

    // Satisfiability per node (exact here because AND is decomposable).
    let mut sat: HashMap<NodeId, bool> = HashMap::new();
    // Literals forced by each node: var -> required phase. Sound
    // under-approximation of entailment, used as determinism evidence.
    let mut forced: HashMap<NodeId, HashMap<Var, bool>> = HashMap::new();
    for &id in &order {
        let node = circuit.node(id).expect("known node");
        let (s, f) = match node {
            Node::True { .. } => (true, HashMap::new()),
            Node::False { .. } => (false, HashMap::new()),
            Node::Lit { var, phase, .. } => (true, HashMap::from([(*var, *phase)])),
            Node::And { children, .. } => {
                let s = children.iter().all(|c| sat[c]);
                let mut f = HashMap::new();
                for c in children {
                    // Scopes are disjoint (pass 1), so no conflicting inserts.
                    f.extend(forced[c].iter().map(|(&v, &p)| (v, p)));
                }
                (s, f)
            }
            Node::Or { children, .. } => {
                let s = children.iter().any(|c| sat[c]);
                let mut iter = children.iter();
                let mut f = forced[iter.next().expect("non-empty or")].clone();
                for c in iter {
                    let other = &forced[c];
                    f.retain(|v, p| other.get(v) == Some(p));
                }
                (s, f)
            }
        };
        sat.insert(id, s);
        forced.insert(id, f);
    }

    // Pass 2: OR determinism, evidence first, enumeration as fallback.
    let mut or_checks = Vec::new();
    for &id in &order {
        if let Node::Or { children, .. } = circuit.node(id).expect("known node") {
            for i in 0..children.len() {
                for j in (i + 1)..children.len() {
                    let (a, b) = (children[i], children[j]);
                    let evidence = if !sat[&a] {
                        DeterminismEvidence::UnsatChild { child: a }
                    } else if !sat[&b] {
                        DeterminismEvidence::UnsatChild { child: b }
                    } else if let Some(var) = conflicting_forced(&forced[&a], &forced[&b]) {
                        DeterminismEvidence::ForcedLiteral { var }
                    } else {
                        let joined: BTreeSet<Var> =
                            scopes[&a].union(&scopes[&b]).copied().collect();
                        if joined.len() as u32 <= config.determinism_enumeration_threshold {
                            match enumeration_check(circuit, a, b, &joined) {
                                Ok(checked) => DeterminismEvidence::Enumeration {
                                    assignments_checked: checked,
                                },
                                Err(witness) => {
                                    return Err(Rejection::OrNotDeterministic {
                                        node: id,
                                        pair: (a, b),
                                        witness,
                                    });
                                }
                            }
                        } else {
                            return Err(Rejection::Undecidable {
                                node: id,
                                pair: (a, b),
                                scope_size: joined.len(),
                            });
                        }
                    };
                    or_checks.push(OrCheckRecord {
                        node: id,
                        pair: (a, b),
                        evidence,
                    });
                }
            }
        }
    }

    Ok(ValidationProof {
        and_checks,
        or_checks,
    })
}

/// First variable on which two forced-literal maps disagree.
fn conflicting_forced(a: &HashMap<Var, bool>, b: &HashMap<Var, bool>) -> Option<Var> {
    a.iter()
        .find_map(|(&v, &p)| match b.get(&v) {
            Some(&q) if q != p => Some(v),
            _ => None,
        })
}

/// Exhaustively check that no assignment over `joined` satisfies both
/// children. Ok(number of assignments checked) when disjoint, Err with
/// a witnessing assignment otherwise.
fn enumeration_check(
    circuit: &Circuit,
    a: NodeId,
    b: NodeId,
    joined: &BTreeSet<Var>,
) -> Result<u64, Vec<(Var, bool)>> {
    let vars: Vec<Var> = joined.iter().copied().collect();
    let n = vars.len();
    debug_assert!(n <= 63);
    let total = 1u64 << n;
    for mask in 0u64..total {
        let assignment = |v: Var| {
            vars.iter()
                .position(|&x| x == v)
                .map(|i| (mask >> i) & 1 == 1)
                .unwrap_or(false)
        };
        if circuit.eval(a, &assignment) && circuit.eval(b, &assignment) {
            return Err(vars.iter().map(|&v| (v, assignment(v))).collect());
        }
    }
    Ok(total)
}
