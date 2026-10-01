//! Startup configuration loaded from `config/pctl.toml` with env overrides.
//!
//! Env vars (all optional, evaluated after the file):
//! `PCTL_BIND_ADDR`, `PCTL_MEMORY_BUDGET_BYTES`,
//! `PCTL_SPILL_DIR`, `PCTL_SPILL_MAX_BYTES`, `PCTL_GROUP_TABLE_CAP`.

use std::env;
use std::path::PathBuf;

use crate::error::{ErrorKind, PctlError, Result};

#[derive(Debug, Clone)]
pub struct Config {
    pub bind_addr: String,
    /// Per-query total resident budget, including the string accumulator.
    pub memory_budget_bytes: usize,
    pub spill_dir: PathBuf,
    pub max_spill_bytes: u64,
    /// Hard cap on simultaneously tracked groups (skew protection).
    pub group_table_cap: usize,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            bind_addr: "127.0.0.1:8080".into(),
            memory_budget_bytes: 64 * 1024 * 1024,
            spill_dir: std::env::temp_dir().join("pctl-spill"),
            max_spill_bytes: 1 << 30,
            group_table_cap: 100_000,
        }
    }
}

/// Mirror of the TOML document.
#[derive(serde::Deserialize, Default)]
struct ConfigFile {
    #[serde(rename = "server")]
    server: Option<ServerSection>,
    #[serde(rename = "execution")]
    execution: Option<ExecutionSection>,
}

#[derive(serde::Deserialize, Default)]
struct ServerSection {
    bind_addr: Option<String>,
}

#[derive(serde::Deserialize, Default)]
struct ExecutionSection {
    memory_budget_bytes: Option<usize>,
    spill_dir: Option<PathBuf>,
    max_spill_bytes: Option<u64>,
    group_table_cap: Option<usize>,
}

impl Config {
    /// Load `config/pctl.toml` if present (missing file is fine), then apply
    /// environment overrides, then fail fast on nonsensical values.
    pub fn load(path: impl Into<PathBuf>) -> Result<Self> {
        let path = path.into();
        let mut cfg = Config::default();
        if let Ok(text) = std::fs::read_to_string(&path) {
            let file: ConfigFile = toml::from_str(&text).map_err(|e| {
                PctlError::new(
                    ErrorKind::InvalidRequest,
                    "bad_config",
                    format!("invalid config {}: {e}", path.display()),
                )
            })?;
            if let Some(s) = file.server {
                if let Some(v) = s.bind_addr {
                    cfg.bind_addr = v;
                }
            }
            if let Some(x) = file.execution {
                if let Some(v) = x.memory_budget_bytes {
                    cfg.memory_budget_bytes = v;
                }
                if let Some(v) = x.spill_dir {
                    cfg.spill_dir = v;
                }
                if let Some(v) = x.max_spill_bytes {
                    cfg.max_spill_bytes = v;
                }
                if let Some(v) = x.group_table_cap {
                    cfg.group_table_cap = v;
                }
            }
        }
        if let Ok(v) = env::var("PCTL_BIND_ADDR") {
            cfg.bind_addr = v;
        }
        if let Ok(v) = env::var("PCTL_MEMORY_BUDGET_BYTES") {
            cfg.memory_budget_bytes = parse_usize(&v, "PCTL_MEMORY_BUDGET_BYTES")?;
        }
        if let Ok(v) = env::var("PCTL_SPILL_DIR") {
            cfg.spill_dir = v.into();
        }
        if let Ok(v) = env::var("PCTL_SPILL_MAX_BYTES") {
            cfg.max_spill_bytes = v
                .parse::<u64>()
                .map_err(|_| config_err("PCTL_SPILL_MAX_BYTES", &v))?;
        }
        if let Ok(v) = env::var("PCTL_GROUP_TABLE_CAP") {
            cfg.group_table_cap = parse_usize(&v, "PCTL_GROUP_TABLE_CAP")?;
        }
        let cfg = cfg.validate()?;
        Ok(cfg)
    }

    fn validate(self) -> Result<Self> {
        if self.memory_budget_bytes < 1024 {
            return Err(PctlError::validation(
                "memory_budget_too_small",
                "memory_budget_bytes must be >= 1024 (one KiB)",
            ));
        }
        if self.group_table_cap == 0 {
            return Err(PctlError::validation(
                "group_table_cap_zero",
                "group_table_cap must be >= 1",
            ));
        }
        if self.bind_addr.is_empty() {
            return Err(PctlError::validation(
                "empty_bind_addr",
                "bind_addr must be non-empty",
            ));
        }
        Ok(self)
    }

    pub fn ensure_spill_dir(&self) -> Result<()> {
        std::fs::create_dir_all(&self.spill_dir)?;
        Ok(())
    }
}

fn parse_usize(v: &str, var: &str) -> Result<usize> {
    v.parse::<usize>().map_err(|_| config_err(var, v))
}

fn config_err(var: &str, v: &str) -> PctlError {
    PctlError::validation(
        "bad_env_config",
        format!("environment variable {var}={v:?} is not a valid non-negative integer"),
    )
}
