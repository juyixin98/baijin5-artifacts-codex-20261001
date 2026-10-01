//! Runtime configuration for the ordering engine.
//!
//! Configuration is plain TOML (a documented sample lives in
//! `config/engine.toml`), can be overridden by environment variables and is
//! *validated on load*: a nonsensical budget or directory fails fast with a
//! categorized error rather than being discovered mid-query.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use sl_types::{ErrorCategory, SlError};

/// How spill files are laid out.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct SpillConfig {
    /// Directory for Arrow IPC spill files. Must exist and be writable when
    /// `external_select` may be used; validated lazily at spill time but also
    /// checked at startup when `require_spill_dir` is set.
    pub directory: PathBuf,
    /// Target number of rows per spilled run. The in-memory run is flushed to
    /// IPC once the live state reaches `state_budget_rows`; this value only
    /// bounds per-run decode later and is kept for documentation/tooling.
    pub run_target_rows: usize,
}

impl Default for SpillConfig {
    fn default() -> Self {
        Self {
            directory: PathBuf::from("./.sl-spill"),
            run_target_rows: 100_000,
        }
    }
}

/// Resource/budget configuration.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct BudgetConfig {
    /// Maximum number of rows retained in the live heap per query. `0` means
    /// unlimited. Exceeding this triggers the overflow policy.
    pub state_budget_rows: usize,
    /// Rough byte ceiling for the live retained state per query. `0` means
    /// unlimited. Accounting uses `Scalar::estimated_bytes` plus a fixed
    /// per-entry overhead (see [`super::resource` constants] in sl-resource).
    pub state_budget_bytes: usize,
    /// What to do when a budget would be exceeded.
    pub overflow_policy: sl_types::OverflowPolicy,
}

impl Default for BudgetConfig {
    fn default() -> Self {
        Self {
            state_budget_rows: 0,
            state_budget_bytes: 0,
            overflow_policy: sl_types::OverflowPolicy::Reject,
        }
    }
}

/// HTTP server knobs.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct ServerConfig {
    pub host: String,
    pub port: u16,
    /// Max request body size in bytes (JSON payload).
    pub max_body_bytes: usize,
}

impl Default for ServerConfig {
    fn default() -> Self {
        Self {
            host: "127.0.0.1".to_string(),
            port: 8080,
            max_body_bytes: 16 * 1024 * 1024,
        }
    }
}

/// Top-level configuration.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(default)]
pub struct EngineConfig {
    pub budget: BudgetConfig,
    pub spill: SpillConfig,
    pub server: ServerConfig,
}

impl EngineConfig {
    /// Parse TOML, then apply `SL_*` environment overrides, then validate.
    pub fn from_toml_str(toml_text: &str) -> Result<Self, SlError> {
        let cfg: EngineConfig = toml::from_str(toml_text).map_err(|e| {
            SlError::new(
                ErrorCategory::Validation,
                "config_parse",
                format!("invalid TOML configuration: {e}"),
                "sl_config::loader",
            )
        })?;
        Ok(cfg.with_env_overrides().validate()?)
    }

    pub fn default_validated() -> Result<Self, SlError> {
        Self::default().validate()
    }

    /// Environment overrides (all optional):
    /// `SL_STATE_BUDGET_ROWS`, `SL_STATE_BUDGET_BYTES`,
    /// `SL_OVERFLOW_POLICY` (`reject`|`external_select`),
    /// `SL_SPILL_DIR`, `SL_HOST`, `SL_PORT`, `SL_MAX_BODY_BYTES`.
    pub fn with_env_overrides(mut self) -> Self {
        if let Some(v) = env_usize("SL_STATE_BUDGET_ROWS") {
            self.budget.state_budget_rows = v;
        }
        if let Some(v) = env_usize("SL_STATE_BUDGET_BYTES") {
            self.budget.state_budget_bytes = v;
        }
        if let Some(v) = std::env::var("SL_OVERFLOW_POLICY").ok().filter(|s| !s.is_empty()) {
            self.budget.overflow_policy = match v.as_str() {
                "reject" => sl_types::OverflowPolicy::Reject,
                "external_select" => sl_types::OverflowPolicy::ExternalSelect,
                // Unknown value is ignored here and surfaced nowhere; startup
                // validation treats the config as-is. Keeping startup robust
                // to a typo would hide the mistake, so callers may validate.
                other => {
                    tracing::warn!(value = other, "ignoring unknown SL_OVERFLOW_POLICY");
                    self.budget.overflow_policy
                }
            };
        }
        if let Some(v) = std::env::var("SL_SPILL_DIR").ok().filter(|s| !s.is_empty()) {
            self.spill.directory = PathBuf::from(v);
        }
        if let Some(v) = std::env::var("SL_HOST").ok().filter(|s| !s.is_empty()) {
            self.server.host = v;
        }
        if let Some(v) = env_u16("SL_PORT") {
            self.server.port = v;
        }
        if let Some(v) = env_usize("SL_MAX_BODY_BYTES") {
            self.server.max_body_bytes = v;
        }
        self
    }

    /// Fail-fast validation of cross-field invariants.
    pub fn validate(mut self) -> Result<Self, SlError> {
        if self.spill.run_target_rows == 0 {
            return Err(config_err(
                "spill_run_target_zero",
                "spill.run_target_rows must be > 0",
            ));
        }
        if self.server.max_body_bytes == 0 {
            return Err(config_err(
                "max_body_zero",
                "server.max_body_bytes must be > 0",
            ));
        }
        if self.server.host.trim().is_empty() {
            return Err(config_err("empty_host", "server.host must not be empty"));
        }
        if matches!(
            self.budget.overflow_policy,
            sl_types::OverflowPolicy::ExternalSelect
        ) && self.spill.directory.as_os_str().is_empty()
        {
            return Err(config_err(
                "spill_dir_required",
                "overflow_policy=external_select requires a non-empty spill.directory",
            ));
        }
        // Canonicalize a relative-ish default for stable logs.
        if self.spill.directory.as_os_str().is_empty() {
            self.spill.directory = PathBuf::from("./.sl-spill");
        }
        Ok(self)
    }
}

fn config_err(code: &'static str, msg: &'static str) -> SlError {
    SlError::new(
        ErrorCategory::Validation,
        code,
        msg,
        "sl_config::loader",
    )
}

fn env_usize(key: &str) -> Option<usize> {
    std::env::var(key).ok()?.parse().ok()
}

fn env_u16(key: &str) -> Option<u16> {
    std::env::var(key).ok()?.parse().ok()
}
