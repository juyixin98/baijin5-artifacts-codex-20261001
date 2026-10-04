use crate::syntax::Node;
use std::collections::{BTreeMap, BTreeSet};

/// Literals forced in every satisfying assignment of a node.
/// `Unsat` means the node has no satisfying assignment at all.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Forced {
    Unsat,
    Set(BTreeSet<(u32, bool)>),
}

pub fn forced_literals(node: &Node) -> Forced {
    match node {
        Node::Lit { var, polarity } => {
            Forced::Set([(*var, *polarity)].into_iter().collect())
        }
        Node::Const { value } => {
            if *value {
                Forced::Set(BTreeSet::new())
            } else {
                Forced::Unsat
            }
        }
        Node::And { children } => {
            let mut acc = BTreeSet::new();
            for child in children {
                match forced_literals(child) {
                    Forced::Unsat => return Forced::Unsat,
                    Forced::Set(set) => acc.extend(set),
                }
            }
            Forced::Set(acc)
        }
        Node::Or { children } => {
            let mut acc: Option<BTreeSet<(u32, bool)>> = None;
            for child in children {
                match forced_literals(child) {
                    Forced::Unsat => continue,
                    Forced::Set(set) => {
                        acc = Some(match acc {
                            None => set,
                            Some(prev) => prev.intersection(&set).copied().collect(),
                        });
                    }
                }
            }
            match acc {
                None => Forced::Unsat,
                Some(set) => Forced::Set(set),
            }
        }
    }
}

pub fn eval(node: &Node, assignment: &BTreeMap<u32, bool>) -> bool {
    match node {
        Node::Lit { var, polarity } => {
            assignment.get(var).copied().unwrap_or(false) == *polarity
        }
        Node::And { children } => children.iter().all(|c| eval(c, assignment)),
        Node::Or { children } => children.iter().any(|c| eval(c, assignment)),
        Node::Const { value } => *value,
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PairVerdict {
    /// Syntactic proof that the two children can never both hold.
    Evidence(String),
    /// Exhaustive check over the pair's variable union found no
    /// assignment satisfying both.
    ExhaustiveDisjoint { assignments_checked: u64 },
    /// Exhaustive check found an assignment satisfying both children.
    Violated { witness: BTreeMap<u32, bool> },
    /// No evidence and the variable scope is too large to enumerate.
    Unknown,
}

pub fn check_pair(a: &Node, b: &Node, max_exhaustive_vars: u32) -> PairVerdict {
    match (forced_literals(a), forced_literals(b)) {
        (Forced::Unsat, _) => {
            return PairVerdict::Evidence(
                "left child is unsatisfiable, pair is vacuously disjoint".to_string(),
            )
        }
        (_, Forced::Unsat) => {
            return PairVerdict::Evidence(
                "right child is unsatisfiable, pair is vacuously disjoint".to_string(),
            )
        }
        (Forced::Set(sa), Forced::Set(sb)) => {
            for (var, polarity) in &sa {
                if sb.contains(&(*var, !*polarity)) {
                    return PairVerdict::Evidence(format!(
                        "opposed forced literal on variable x{var}"
                    ));
                }
            }
        }
    }

    let mut vars = a.var_set();
    vars.extend(b.var_set());
    if vars.len() as u32 > max_exhaustive_vars || vars.len() >= 63 {
        return PairVerdict::Unknown;
    }
    let vars: Vec<u32> = vars.into_iter().collect();
    let total = 1u64 << vars.len();
    for mask in 0..total {
        let mut assignment = BTreeMap::new();
        for (idx, var) in vars.iter().enumerate() {
            assignment.insert(*var, (mask >> idx) & 1 == 1);
        }
        if eval(a, &assignment) && eval(b, &assignment) {
            return PairVerdict::Violated { witness: assignment };
        }
    }
    PairVerdict::ExhaustiveDisjoint {
        assignments_checked: total,
    }
}

pub fn format_witness(witness: &BTreeMap<u32, bool>) -> String {
    witness
        .iter()
        .map(|(var, value)| format!("x{var}={value}"))
        .collect::<Vec<_>>()
        .join(",")
}
