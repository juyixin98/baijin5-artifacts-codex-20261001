//! Local configuration. Loaded from environment with built-in defaults; no
//! external config service. Tests construct [`Settings`] directly instead of
//! touching the environment.

/// Process settings.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Settings {
    /// Socket address the Axum server binds.
    pub bind_addr: String,
    /// Maximum number of input rows accepted per request.
    pub max_rows: usize,
    /// Maximum byte length of a single value.
    pub max_value_bytes: usize,
    /// When true, diagnostics log only redacted value previews.
    pub redact_sensitive: bool,
    /// How many recent diagnostics records the in-memory state retains.
    pub diag_history: usize,
}

impl Default for Settings {
    fn default() -> Self {
        Settings {
            bind_addr: "127.0.0.1:8080".into(),
            max_rows: 100_000,
            max_value_bytes: 1 << 20,
            redact_sensitive: true,
            diag_history: 256,
        }
    }
}

impl Settings {
    /// Build from process environment, falling back to defaults. A malformed
    /// value returns an error rather than being silently ignored.
    pub fn from_env() -> crate::error::Result<Self> {
        let mut s = Settings::default();
        if let Some(v) = env("BIND_ADDR")? {
            s.bind_addr = v;
        }
        if let Some(v) = env("MAX_ROWS")? {
            s.max_rows = parse_usize("MAX_ROWS", &v)?;
        }
        if let Some(v) = env("MAX_VALUE_BYTES")? {
            s.max_value_bytes = parse_usize("MAX_VALUE_BYTES", &v)?;
        }
        if let Some(v) = env("REDACT_SENSITIVE")? {
            s.redact_sensitive = parse_bool("REDACT_SENSITIVE", &v)?;
        }
        if let Some(v) = env("DIAG_HISTORY")? {
            s.diag_history = parse_usize("DIAG_HISTORY", &v)?;
        }
        Ok(s)
    }
}

fn env(key: &str) -> crate::error::Result<Option<String>> {
    match std::env::var(key) {
        Ok(v) => Ok(Some(v)),
        Err(std::env::VarError::NotPresent) => Ok(None),
        Err(std::env::VarError::NotUnicode(_)) => Err(crate::error::AppError::Config(format!(
            "{key} is not valid UTF-8"
        ))),
    }
}

fn parse_usize(key: &str, v: &str) -> crate::error::Result<usize> {
    v.trim()
        .parse()
        .map_err(|_| crate::error::AppError::Config(format!("{key}={v:?} is not a valid usize")))
}

fn parse_bool(key: &str, v: &str) -> crate::error::Result<bool> {
    match v.trim().to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "on" => Ok(true),
        "0" | "false" | "no" | "off" => Ok(false),
        _ => Err(crate::error::AppError::Config(format!(
            "{key}={v:?} is not a valid bool"
        ))),
    }
}
