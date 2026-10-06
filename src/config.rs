//! Runtime configuration, loaded from a JSON file.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Config {
    /// Maximum number of variables in an OR child pair's joined scope
    /// for which determinism may be established by exhaustive
    /// enumeration. Above this, explicit forced-literal evidence is
    /// required, otherwise the node is reported as undecidable.
    pub determinism_enumeration_threshold: u32,
    /// Maximum number of declared variables for which the optional
    /// independent brute-force recount is run.
    pub independent_check_threshold: u32,
    /// Whether to run the independent brute-force recount after the
    /// structural count (small circuits only).
    pub run_independent_check: bool,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            determinism_enumeration_threshold: 16,
            independent_check_threshold: 16,
            run_independent_check: false,
        }
    }
}

impl Config {
    pub fn from_json_str(s: &str) -> Result<Self, serde_json::Error> {
        serde_json::from_str(s)
    }
}
