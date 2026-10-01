//! Process configuration loaded from `config/decorr.toml` (optional) and
//! environment overrides. Everything has a local default; no cloud accounts.

use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub server: ServerConfig,
    pub execution: ExecutionConfig,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ServerConfig {
    pub host: String,
    pub port: u16,
    /// Emit JSON-structured logs instead of human-readable lines.
    pub json_logs: bool,
    pub log_level: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExecutionConfig {
    /// Default for the per-request cross-check flag.
    pub cross_check_by_default: bool,
    /// Maximum number of rows accepted in a single synthetic fixture relation.
    pub max_rows_per_relation: usize,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            server: ServerConfig {
                host: "127.0.0.1".to_string(),
                port: 8080,
                json_logs: true,
                log_level: "info".to_string(),
            },
            execution: ExecutionConfig {
                cross_check_by_default: true,
                max_rows_per_relation: 100_000,
            },
        }
    }
}

impl Config {
    /// Load configuration: built-in defaults, then an optional TOML file, then
    /// environment overrides (`DECORR_PORT`, `DECORR_HOST`, `DECORR_LOG_LEVEL`,
    /// `DECORR_JSON_LOGS`).
    pub fn load(path: Option<&str>) -> Result<Self, ConfigError> {
        let mut config = Config::default();

        let path = path
            .map(std::ffi::OsString::from)
            .or_else(|| std::env::var_os("DECORR_CONFIG"))
            .unwrap_or_else(|| std::ffi::OsString::from("config/decorr.toml"));

        if let Ok(contents) = std::fs::read_to_string(&path) {
            let raw: ConfigToml = toml::from_str(&contents).map_err(|e| ConfigError::Parse {
                path: path.to_string_lossy().into_owned(),
                message: e.to_string(),
            })?;
            if let Some(server) = raw.server {
                if let Some(host) = server.host {
                    config.server.host = host;
                }
                if let Some(port) = server.port {
                    config.server.port = port;
                }
                if let Some(json_logs) = server.json_logs {
                    config.server.json_logs = json_logs;
                }
                if let Some(log_level) = server.log_level {
                    config.server.log_level = log_level;
                }
            }
            if let Some(execution) = raw.execution {
                if let Some(cross_check) = execution.cross_check_by_default {
                    config.execution.cross_check_by_default = cross_check;
                }
                if let Some(max_rows) = execution.max_rows_per_relation {
                    config.execution.max_rows_per_relation = max_rows;
                }
            }
        }

        if let Ok(host) = std::env::var("DECORR_HOST") {
            config.server.host = host;
        }
        if let Ok(port) = std::env::var("DECORR_PORT") {
            config.server.port = port.parse().map_err(|_| ConfigError::Env {
                name: "DECORR_PORT".to_string(),
                value: port,
            })?;
        }
        if let Ok(level) = std::env::var("DECORR_LOG_LEVEL") {
            config.server.log_level = level;
        }
        if let Ok(flag) = std::env::var("DECORR_JSON_LOGS") {
            config.server.json_logs = matches!(flag.as_str(), "1" | "true" | "TRUE");
        }

        Ok(config)
    }

    pub fn bind_address(&self) -> String {
        format!("{}:{}", self.server.host, self.server.port)
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ConfigError {
    #[error("failed to parse config file `{path}`: {message}")]
    Parse { path: String, message: String },
    #[error("invalid environment variable {name}: `{value}`")]
    Env { name: String, value: String },
}

// All-optional TOML mirror so partial config files are accepted.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ConfigToml {
    #[serde(default)]
    server: Option<ServerToml>,
    #[serde(default)]
    execution: Option<ExecutionToml>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ServerToml {
    #[serde(default)]
    host: Option<String>,
    #[serde(default)]
    port: Option<u16>,
    #[serde(default)]
    json_logs: Option<bool>,
    #[serde(default)]
    log_level: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ExecutionToml {
    #[serde(default)]
    cross_check_by_default: Option<bool>,
    #[serde(default)]
    max_rows_per_relation: Option<usize>,
}
