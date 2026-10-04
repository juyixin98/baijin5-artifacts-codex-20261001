use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum Node {
    Lit { var: u32, polarity: bool },
    And { children: Vec<Node> },
    Or { children: Vec<Node> },
    Const { value: bool },
}

impl Node {
    pub fn var_set(&self) -> BTreeSet<u32> {
        let mut out = BTreeSet::new();
        self.collect_vars(&mut out);
        out
    }

    fn collect_vars(&self, out: &mut BTreeSet<u32>) {
        match self {
            Node::Lit { var, .. } => {
                out.insert(*var);
            }
            Node::And { children } | Node::Or { children } => {
                for c in children {
                    c.collect_vars(out);
                }
            }
            Node::Const { .. } => {}
        }
    }
}
