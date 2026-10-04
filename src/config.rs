//! Configuration for the independent checker.
//!
//! Resource limits are deliberate: exceeding any of them must make the checker
//! answer "unverified" rather than risk accepting a proof it could not fully
//! inspect. Limits are tunable via TOML and via CLI overrides.

use serde::Deserialize;
use std::path::Path;

/// Hard safety valves. All values are generous defaults for synthetic fixtures
/// but small enough that exhaustion is directly testable.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(default)]
pub struct ResourceLimits {
    /// Maximum number of clause nodes retained from the stream.
    pub max_clauses: usize,
    /// Maximum literals in a single parsed/derived clause.
    pub max_clause_width: usize,
    /// Maximum resolution steps processed.
    pub max_steps: usize,
    /// Maximum variable id allowed in any literal.
    pub max_variable: u32,
    /// Maximum bytes per input line.
    pub max_line_bytes: usize,
}

impl Default for ResourceLimits {
    fn default() -> Self {
        ResourceLimits {
            max_clauses: 1_000_000,
            max_clause_width: 100_000,
            max_steps: 1_000_000,
            max_variable: 1_000_000,
            max_line_bytes: 16 * 1024 * 1024,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(default)]
pub struct Config {
    pub limits: ResourceLimits,
    /// Emit one structured JSON log line per key event to stderr.
    pub log_json: bool,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            limits: ResourceLimits::default(),
            log_json: true,
        }
    }
}

/// Error returned when the configuration file cannot be loaded.
#[derive(Debug)]
pub struct ConfigError(pub String);

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "config error: {}", self.0)
    }
}

impl std::error::Error for ConfigError {}

impl Config {
    /// Load a TOML config file. Missing optional fields fall back to defaults.
    pub fn from_toml_path(path: &Path) -> Result<Self, ConfigError> {
        let text = std::fs::read_to_string(path)
            .map_err(|e| ConfigError(format!("cannot read {}: {e}", path.display())))?;
        toml::from_str(&text)
            .map_err(|e| ConfigError(format!("invalid TOML in {}: {e}", path.display())))
    }
}
