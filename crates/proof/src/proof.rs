//! Serializable resolution proof DAG.
//!
//! Nodes are indexed by their position in [`Proof::nodes`]. Leaf nodes carry
//! the side (`A` antecedent or `B` consequent) from which the clause
//! originates; internal nodes record exactly one pivot variable and two
//! antecedent node indices.

use crate::literal::Clause;
use serde::{Deserialize, Serialize};

/// Which side of the interpolation problem a hypothesis clause came from.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Side {
    /// Antecedent side, commonly called `A`.
    A,
    /// Consequent side, commonly called `B`.
    B,
}

impl Side {
    pub fn label(&self) -> &'static str {
        match self {
            Side::A => "A",
            Side::B => "B",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum ProofNode {
    /// Input clause taken directly from one side's CNF.
    Hypothesis {
        side: Side,
        clause: Clause,
        /// Optional origin description such as "tseitin:!g_3".
        origin: String,
    },
    /// Binary resolution step.
    Resolve {
        pivot: String,
        positive: usize,
        negative: usize,
        clause: Clause,
    },
}

impl ProofNode {
    pub fn clause(&self) -> &Clause {
        match self {
            ProofNode::Hypothesis { clause, .. } => clause,
            ProofNode::Resolve { clause, .. } => clause,
        }
    }
}

/// A proof is an acyclic collection of nodes whose root derives the empty
/// clause. The representation is a DAG because derived clauses are reused.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Proof {
    pub nodes: Vec<ProofNode>,
    pub root: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProofValidationError {
    NodeIndexOutOfBounds { at: usize, referenced: usize },
    RootOutOfBounds { root: usize, len: usize },
    ResolutionClausesMismatch { at: usize },
    RootNotEmptyClause,
}

impl Proof {
    pub fn new(nodes: Vec<ProofNode>, root: usize) -> Self {
        Proof { nodes, root }
    }

    pub fn root_node(&self) -> Option<&ProofNode> {
        self.nodes.get(self.root)
    }

    /// Structural validation only: indices resolve, each resolution step is a
    /// legal application of the resolution rule, and the root is the empty
    /// clause. Interpolant-specific checks live elsewhere.
    pub fn validate(&self) -> Result<(), ProofValidationError> {
        let len = self.nodes.len();
        if self.root >= len {
            return Err(ProofValidationError::RootOutOfBounds {
                root: self.root,
                len,
            });
        }
        for (index, node) in self.nodes.iter().enumerate() {
            if let ProofNode::Resolve {
                pivot,
                positive,
                negative,
                clause,
            } = node
            {
                for referenced in [positive, negative] {
                    if *referenced >= len {
                        return Err(ProofValidationError::NodeIndexOutOfBounds {
                            at: index,
                            referenced: *referenced,
                        });
                    }
                    if *referenced >= index {
                        return Err(ProofValidationError::NodeIndexOutOfBounds {
                            at: index,
                            referenced: *referenced,
                        });
                    }
                }
                let pos_clause = &self.nodes[*positive].clause();
                let neg_clause = &self.nodes[*negative].clause();
                let expected = pos_clause.resolve(neg_clause, pivot);
                match expected {
                    Some(expected_clause) if &expected_clause == clause => {}
                    _ => {
                        return Err(ProofValidationError::ResolutionClausesMismatch { at: index });
                    }
                }
            }
        }
        if !self.nodes[self.root].clause().is_empty() {
            return Err(ProofValidationError::RootNotEmptyClause);
        }
        Ok(())
    }

    /// Return every hypothesis clause for the requested side in node order.
    pub fn hypotheses(&self, side: Side) -> Vec<(usize, &Clause)> {
        self.nodes
            .iter()
            .enumerate()
            .filter_map(|(index, node)| match node {
                ProofNode::Hypothesis {
                    side: node_side,
                    clause,
                    ..
                } if *node_side == side => Some((index, clause)),
                _ => None,
            })
            .collect()
    }
}

/// Incremental builder used by the proving core.
#[derive(Debug, Default)]
pub struct ProofBuilder {
    nodes: Vec<ProofNode>,
}

impl ProofBuilder {
    pub fn new() -> Self {
        ProofBuilder { nodes: Vec::new() }
    }

    pub fn len(&self) -> usize {
        self.nodes.len()
    }

    pub fn is_empty(&self) -> bool {
        self.nodes.is_empty()
    }

    pub fn add_hypothesis(&mut self, side: Side, clause: Clause, origin: String) -> usize {
        self.nodes.push(ProofNode::Hypothesis {
            side,
            clause,
            origin,
        });
        self.nodes.len() - 1
    }

    pub fn add_resolution(
        &mut self,
        pivot: String,
        positive: usize,
        negative: usize,
        clause: Clause,
    ) -> usize {
        self.nodes.push(ProofNode::Resolve {
            pivot,
            positive,
            negative,
            clause,
        });
        self.nodes.len() - 1
    }

    pub fn node(&self, index: usize) -> Option<&ProofNode> {
        self.nodes.get(index)
    }

    pub fn clone_nodes(&self) -> Vec<ProofNode> {
        self.nodes.clone()
    }

    pub fn finish(self, root: usize) -> Proof {
        Proof::new(self.nodes, root)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validates_tiny_empty_clause_proof() {
        let mut builder = ProofBuilder::new();
        let p = builder.add_hypothesis(
            Side::A,
            Clause::unit(Literal::positive("x")),
            "A:1".to_string(),
        );
        let q = builder.add_hypothesis(
            Side::B,
            Clause::unit(Literal::negative("x")),
            "B:1".to_string(),
        );
        let root = builder.add_resolution("x".to_string(), p, q, Clause::empty());
        let proof = builder.finish(root);
        assert!(proof.validate().is_ok());
        assert_eq!(proof.root_node().unwrap().clause(), &Clause::empty());
    }

    #[test]
    fn detects_backward_edge() {
        let mut builder = ProofBuilder::new();
        let _a = builder.add_hypothesis(
            Side::A,
            Clause::unit(Literal::positive("x")),
            "A:1".to_string(),
        );
        let root = builder.add_resolution(
            "x".to_string(),
            0,
            5,
            Clause::empty(),
        );
        let proof = builder.finish(root);
        assert!(proof.validate().is_err());
    }
}

