use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Config {
    /// Max variables in an OR-child pair union for the exhaustive
    /// determinism check to run. Above this, without syntactic
    /// evidence, the pair is reported as undecidable.
    pub exhaustive_determinism_max_vars: u32,
    /// Max declared variables for the independent brute-force
    /// truth-table cross-check.
    pub independent_check_max_vars: u32,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            exhaustive_determinism_max_vars: 20,
            independent_check_max_vars: 20,
        }
    }
}

impl Config {
    pub fn load(path: &std::path::Path) -> Result<Self, String> {
        let text = std::fs::read_to_string(path)
            .map_err(|e| format!("cannot read {}: {e}", path.display()))?;
        serde_json::from_str(&text)
            .map_err(|e| format!("cannot parse {}: {e}", path.display()))
    }
}
