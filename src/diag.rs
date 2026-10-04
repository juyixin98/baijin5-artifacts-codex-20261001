use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Severity {
    Info,
    Reject,
    Undecidable,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DiagCode {
    RequestReceived,
    VarOutOfRange,
    AndDecomposableOk,
    AndNotDecomposable,
    OrDeterminismEvidence,
    OrDeterminismExhaustive,
    OrNotDeterministic,
    DeterminismUndecidable,
    IndependentCheckMatch,
    IndependentCheckMismatch,
    IndependentCheckSkipped,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Diagnostic {
    pub request_id: String,
    pub severity: Severity,
    pub code: DiagCode,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
    pub message: String,
}

impl Diagnostic {
    pub fn new(
        request_id: &str,
        severity: Severity,
        code: DiagCode,
        path: Option<String>,
        message: String,
    ) -> Self {
        Self {
            request_id: request_id.to_string(),
            severity,
            code,
            path,
            message,
        }
    }
}

/// Mask a sensitive reference, keeping only the first and last two
/// characters. Anything shorter is fully masked.
pub fn mask_sensitive(value: &str) -> String {
    let chars: Vec<char> = value.chars().collect();
    if chars.len() <= 4 {
        return "****".to_string();
    }
    let head: String = chars[..2].iter().collect();
    let tail: String = chars[chars.len() - 2..].iter().collect();
    format!("{head}****{tail}")
}
