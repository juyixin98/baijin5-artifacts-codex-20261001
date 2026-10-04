use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProofOp {
    Leaf,
    AndProduct,
    OrChildSmooth,
    OrSum,
    RootSmooth,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProofEntry {
    pub seq: usize,
    pub path: String,
    pub op: ProofOp,
    pub detail: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub smooth_factor_exp: Option<u64>,
    pub count: String,
}

#[derive(Debug, Default, Clone, Serialize, Deserialize)]
pub struct ProofLog {
    pub entries: Vec<ProofEntry>,
}

impl ProofLog {
    pub fn push(
        &mut self,
        path: &str,
        op: ProofOp,
        detail: String,
        smooth_factor_exp: Option<u64>,
        count: String,
    ) {
        let seq = self.entries.len();
        self.entries.push(ProofEntry {
            seq,
            path: path.to_string(),
            op,
            detail,
            smooth_factor_exp,
            count,
        });
    }
}
