//! JSON request/response contract for the service.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct InterpolateRequest {
    pub request_id: String,
    pub antecedent: String,
    pub consequent: String,
    #[serde(default)]
    pub budget: Option<u64>,
    #[serde(default)]
    pub include_proof: Option<bool>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum InterpolateStatus {
    Proved {
        interpolant: String,
        common_variables: Vec<String>,
        verification: VerificationSummary,
        proof: Option<ProofSummary>,
        metrics: Metrics,
    },
    Rejected {
        category: RejectionCategory,
        detail: serde_json::Value,
    },
    InvalidInput {
        errors: Vec<String>,
    },
    Unknown {
        reason: String,
        detail: serde_json::Value,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RejectionCategory {
    JointlySatisfiable,
    VerificationFailed,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct VerificationSummary {
    pub accepted: bool,
    pub failures: Vec<serde_json::Value>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ProofSummary {
    pub node_count: usize,
    pub root: usize,
    pub hypothesis_count: usize,
    pub resolution_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Metrics {
    pub resolutions_used: u64,
    pub variables_eliminated: u32,
    pub elapsed_micros: u128,
}

#[derive(Debug, Clone, Serialize)]
pub struct ServiceResponse {
    pub request_id: String,
    pub engine: EngineIdentity,
    pub status: InterpolateStatus,
    pub trace: Vec<TraceEntry>,
}

#[derive(Debug, Clone, Serialize)]
pub struct EngineIdentity {
    pub name: String,
    pub version: String,
    pub site: String,
    pub modules: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct TraceEntry {
    pub step: u32,
    pub module: String,
    pub level: String,
    pub message: String,
}
