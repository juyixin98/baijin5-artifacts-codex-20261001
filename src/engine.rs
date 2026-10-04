use crate::syntax::Node;
use num_bigint::BigUint;

pub fn count(node: &Node) -> BigUint {
    match node {
        Node::Lit { .. } => BigUint::from(1u32),
        Node::Const { value } => {
            if *value {
                BigUint::from(1u32)
            } else {
                BigUint::from(0u32)
            }
        }
        Node::And { children } => children
            .iter()
            .map(count)
            .fold(BigUint::from(1u32), |a, b| a * b),
        Node::Or { children } => {
            let total = node.var_set().len() as u64;
            children
                .iter()
                .map(|c| {
                    let missing = total - c.var_set().len() as u64;
                    count(c) * (BigUint::from(1u32) << missing)
                })
                .fold(BigUint::from(0u32), |a, b| a + b)
        }
    }
}
