//! Logic circuit syntax: AST types, JSON (de)serialization, scope
//! computation and plain assignment evaluation.
//!
//! A circuit is a DAG of nodes. Leaves are constants or literals;
//! internal nodes are AND / OR with at least one child. Structural
//! restrictions (AND decomposability, OR determinism) are *not*
//! enforced here; they are checked by the validate module.

use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

pub type NodeId = u32;
pub type Var = u32;

/// One node of the circuit DAG.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Node {
    /// Constant true (satisfied by the single empty assignment).
    True { id: NodeId },
    /// Constant false.
    False { id: NodeId },
    /// A literal: variable `var` with polarity `phase`.
    Lit { id: NodeId, var: Var, phase: bool },
    /// Conjunction of children (must be decomposable to be countable).
    And { id: NodeId, children: Vec<NodeId> },
    /// Disjunction of children (must be deterministic to be countable).
    Or { id: NodeId, children: Vec<NodeId> },
}

impl Node {
    pub fn id(&self) -> NodeId {
        match self {
            Node::True { id }
            | Node::False { id }
            | Node::Lit { id, .. }
            | Node::And { id, .. }
            | Node::Or { id, .. } => *id,
        }
    }

    pub fn kind(&self) -> &'static str {
        match self {
            Node::True { .. } => "true",
            Node::False { .. } => "false",
            Node::Lit { .. } => "lit",
            Node::And { .. } => "and",
            Node::Or { .. } => "or",
        }
    }

    pub fn children(&self) -> &[NodeId] {
        match self {
            Node::And { children, .. } | Node::Or { children, .. } => children,
            _ => &[],
        }
    }
}

/// A circuit DAG plus a designated root.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Circuit {
    pub root: NodeId,
    pub nodes: Vec<Node>,
}

/// Errors detected while checking the *syntactic* well-formedness of a
/// circuit (before any semantic validation).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SyntaxError {
    DuplicateNodeId { id: NodeId },
    UnknownRoot { root: NodeId },
    UnknownChild { node: NodeId, child: NodeId },
    EmptyChildren { node: NodeId },
    Cycle { node: NodeId },
}

impl std::fmt::Display for SyntaxError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SyntaxError::DuplicateNodeId { id } => write!(f, "duplicate node id {id}"),
            SyntaxError::UnknownRoot { root } => write!(f, "root node {root} not found"),
            SyntaxError::UnknownChild { node, child } => {
                write!(f, "node {node} references unknown child {child}")
            }
            SyntaxError::EmptyChildren { node } => {
                write!(f, "node {node} has no children")
            }
            SyntaxError::Cycle { node } => write!(f, "cycle detected at node {node}"),
        }
    }
}

impl Circuit {
    /// Check well-formedness and return node ids in topological order
    /// (children before parents), covering everything reachable from root.
    pub fn topo_order(&self) -> Result<Vec<NodeId>, SyntaxError> {
        use std::collections::HashMap;
        let mut by_id: HashMap<NodeId, &Node> = HashMap::new();
        for n in &self.nodes {
            if by_id.insert(n.id(), n).is_some() {
                return Err(SyntaxError::DuplicateNodeId { id: n.id() });
            }
        }
        if !by_id.contains_key(&self.root) {
            return Err(SyntaxError::UnknownRoot { root: self.root });
        }
        for n in &self.nodes {
            if matches!(n, Node::And { .. } | Node::Or { .. }) && n.children().is_empty() {
                return Err(SyntaxError::EmptyChildren { node: n.id() });
            }
            for &c in n.children() {
                if !by_id.contains_key(&c) {
                    return Err(SyntaxError::UnknownChild { node: n.id(), child: c });
                }
            }
        }
        // Iterative DFS from the root; in-stack marks detect cycles.
        let mut order = Vec::new();
        let mut state: HashMap<NodeId, u8> = HashMap::new(); // 1 = in stack, 2 = done
        let mut stack: Vec<(NodeId, bool)> = vec![(self.root, false)];
        while let Some((id, expanded)) = stack.pop() {
            if expanded {
                state.insert(id, 2);
                order.push(id);
                continue;
            }
            match state.get(&id).copied() {
                Some(2) => continue,
                Some(_) => return Err(SyntaxError::Cycle { node: id }),
                None => {}
            }
            state.insert(id, 1);
            stack.push((id, true));
            for &c in by_id[&id].children() {
                if state.get(&c).copied() != Some(2) {
                    stack.push((c, false));
                }
            }
        }
        Ok(order)
    }

    pub fn node(&self, id: NodeId) -> Option<&Node> {
        self.nodes.iter().find(|n| n.id() == id)
    }

    /// Variables mentioned below (and including) each node in `order`.
    /// `order` must come from [`Circuit::topo_order`].
    pub fn scopes(&self, order: &[NodeId]) -> Vec<(NodeId, BTreeSet<Var>)> {
        use std::collections::HashMap;
        let mut memo: HashMap<NodeId, BTreeSet<Var>> = HashMap::new();
        let mut out = Vec::new();
        for &id in order {
            let n = self.node(id).expect("topo order references known nodes");
            let scope = match n {
                Node::True { .. } | Node::False { .. } => BTreeSet::new(),
                Node::Lit { var, .. } => BTreeSet::from([*var]),
                Node::And { .. } | Node::Or { .. } => {
                    let mut s = BTreeSet::new();
                    for &c in n.children() {
                        s.extend(memo[&c].iter().copied());
                    }
                    s
                }
            };
            memo.insert(id, scope.clone());
            out.push((id, scope));
        }
        out
    }

    /// Evaluate the subcircuit rooted at `id` under an assignment.
    pub fn eval(&self, id: NodeId, assignment: &dyn Fn(Var) -> bool) -> bool {
        match self.node(id).expect("eval on known node") {
            Node::True { .. } => true,
            Node::False { .. } => false,
            Node::Lit { var, phase, .. } => assignment(*var) == *phase,
            Node::And { children, .. } => children.iter().all(|&c| self.eval(c, assignment)),
            Node::Or { children, .. } => children.iter().any(|&c| self.eval(c, assignment)),
        }
    }
}
