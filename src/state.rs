//! Resources & state: process configuration and shared application state.

use serde::{Deserialize, Serialize};

use crate::collation::{RepresentativePolicy, RuleVersion};

/// Process configuration, loaded from `config/collate_agg.toml` and/or env overrides.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct AppConfig {
    /// Bind address for the HTTP API.
    pub bind_addr: String,
    /// Default rule version when a request omits one.
    pub default_rule_version: String,
    /// Default representative-value policy.
    pub representative_policy: RepresentativePolicy,
    /// Cross-check core results against the independent oracle on every request.
    pub oracle_crosscheck: bool,
    /// If true, diagnostics never include raw values even when asked.
    pub redact_sensitive: bool,
    /// Max accepted input rows per request (boundary guard against unbounded input).
    pub max_rows: usize,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            bind_addr: "127.0.0.1:8080".to_string(),
            default_rule_version: RuleVersion::V2026R1.as_str().to_string(),
            representative_policy: RepresentativePolicy::FirstWins,
            oracle_crosscheck: true,
            redact_sensitive: true,
            max_rows: 100_000,
        }
    }
}

impl AppConfig {
    /// Load from a TOML file; missing file is allowed (defaults used).
    pub fn load_file(path: &str) -> Result<Self, ConfigError> {
        match std::fs::read_to_string(path) {
            Ok(text) => toml::from_str(&text).map_err(|e| ConfigError::Parse(e.to_string())),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Self::default()),
            Err(e) => Err(ConfigError::Io(e.to_string())),
        }
    }

    /// Apply documented environment overrides on top of `self`.
    pub fn apply_env(mut self) -> Self {
        if let Ok(v) = std::env::var("COLLATE_BIND_ADDR") {
            self.bind_addr = v;
        }
        if let Ok(v) = std::env::var("COLLATE_DEFAULT_RULE") {
            self.default_rule_version = v;
        }
        if let Ok(v) = std::env::var("COLLATE_MAX_ROWS") {
            if let Ok(n) = v.parse() {
                self.max_rows = n;
            }
        }
        self
    }

    pub fn default_version(&self) -> Option<RuleVersion> {
        RuleVersion::parse(&self.default_rule_version)
    }
}

#[derive(Debug)]
pub enum ConfigError {
    Parse(String),
    Io(String),
}

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ConfigError::Parse(m) => write!(f, "config parse error: {m}"),
            ConfigError::Io(m) => write!(f, "config io error: {m}"),
        }
    }
}
impl std::error::Error for ConfigError {}

/// Shared, immutable application state handed to every handler.
#[derive(Debug, Clone)]
pub struct AppState {
    pub config: AppConfig,
}

impl AppState {
    pub fn new(config: AppConfig) -> Self {
        Self { config }
    }
}
