//! Counting request format: the unit of work accepted by the CLI and
//! the library pipeline. Deserialized from JSON via serde.

use crate::syntax::{Circuit, Var};
use serde::{Deserialize, Serialize};

/// A single model-counting request.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Request {
    /// Correlation id echoed into diagnostics and proof records.
    pub request_id: String,
    /// Optional free-text label. Treated as sensitive: diagnostics only
    /// ever carry its redacted fingerprint.
    #[serde(default)]
    pub label: Option<String>,
    /// Declared variable universe. The final count ranges over all
    /// assignments to these variables; variables not occurring in the
    /// circuit contribute smoothing factors of 2 each. When omitted,
    /// the universe is the circuit's own root scope.
    #[serde(default)]
    pub declared_vars: Option<Vec<Var>>,
    pub circuit: Circuit,
}

impl Request {
    pub fn from_json_str(s: &str) -> Result<Self, serde_json::Error> {
        serde_json::from_str(s)
    }
}
