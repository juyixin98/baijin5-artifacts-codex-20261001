//! Startup configuration loaded from `config/groupagg.toml`, with environment
//! variable overrides (`GROUPAGG_*`).  No secrets exist in this service; the
//! config covers host/port, memory budget and the spill directory.

use std::path::{Path, PathBuf};

use serde::Deserialize;

use crate::error::{Error, ErrorKind, Result};
use crate::exec::EngineConfig;

#[derive(Debug, Clone, Deserialize)]
pub struct ConfigFile {
    pub server: ServerSection,
    pub engine: EngineSection,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ServerSection {
    pub host: String,
    pub port: u16,
}

#[derive(Debug, Clone, Deserialize)]
pub struct EngineSection {
    /// Memory budget per query, expressed as a human size string in the TOML
    /// file (e.g. "4MiB"), parsed via [`parse_byte_size`].
    pub memory_budget: String,
    pub spill_dir: PathBuf,
    pub cancel_check_rows: u64,
}

/// Fully resolved runtime configuration.
#[derive(Debug, Clone)]
pub struct Settings {
    pub host: String,
    pub port: u16,
    pub engine: EngineConfig,
}

impl Settings {
    /// Load `<path>` then apply `GROUPAGG_*` environment overrides.
    pub fn load(path: &Path) -> Result<Self> {
        let text = std::fs::read_to_string(path).map_err(|e| {
            Error::new(
                ErrorKind::InvalidRequest,
                format!("cannot read config {}: {e}", path.display()),
            )
        })?;
        let file: ConfigFile = toml::from_str(&text).map_err(|e| {
            Error::new(
                ErrorKind::InvalidRequest,
                format!("invalid config {}: {e}", path.display()),
            )
        })?;
        let memory_budget_bytes = parse_byte_size(&file.engine.memory_budget)?;

        let host = std::env::var("GROUPAGG_HOST").unwrap_or(file.server.host);
        let port = std::env::var("GROUPAGG_PORT")
            .ok()
            .map(|p| p.parse::<u16>())
            .transpose()
            .map_err(|_| Error::invalid_request("GROUPAGG_PORT is not a valid port"))?
            .unwrap_or(file.server.port);
        let memory_budget_bytes = std::env::var("GROUPAGG_MEMORY_BUDGET")
            .ok()
            .map(|s| parse_byte_size(&s))
            .transpose()?
            .unwrap_or(memory_budget_bytes);
        let spill_root = std::env::var("GROUPAGG_SPILL_DIR")
            .map(PathBuf::from)
            .unwrap_or(file.engine.spill_dir);
        let cancel_check_rows = std::env::var("GROUPAGG_CANCEL_CHECK_ROWS")
            .ok()
            .map(|s| s.parse::<u64>())
            .transpose()
            .map_err(|_| {
                Error::invalid_request("GROUPAGG_CANCEL_CHECK_ROWS must be a positive integer")
            })?
            .unwrap_or(file.engine.cancel_check_rows)
            .max(1);

        std::fs::create_dir_all(&spill_root).map_err(|e| {
            Error::new(
                ErrorKind::SpillIo,
                format!("cannot create spill dir {}: {e}", spill_root.display()),
            )
        })?;

        Ok(Settings {
            host,
            port,
            engine: EngineConfig {
                memory_budget_bytes,
                spill_root,
                cancel_check_rows,
            },
        })
    }
}

/// Parse sizes like `"4MiB"`, `"512KiB"`, `"1GB"`, `"1024"` (bytes).
/// Accepts binary (KiB/MiB/GiB) and decimal (KB/MB/GB) suffixes.
pub fn parse_byte_size(input: &str) -> Result<usize> {
    let s = input.trim();
    let split_at = s
        .find(|c: char| !c.is_ascii_digit() && c != '.')
        .unwrap_or(s.len());
    let (num, suffix) = s.split_at(split_at);
    let value: f64 = num
        .parse()
        .map_err(|_| Error::invalid_request(format!("invalid byte size '{input}'")))?;
    let multiplier: usize = match suffix.trim().to_ascii_uppercase().as_str() {
        "" | "B" => 1,
        "KIB" => 1 << 10,
        "MIB" => 1 << 20,
        "GIB" => 1 << 30,
        "K" | "KB" => 1_000,
        "M" | "MB" => 1_000_000,
        "G" | "GB" => 1_000_000_000,
        other => {
            return Err(Error::invalid_request(format!(
                "unknown size suffix '{other}' in '{input}'"
            )))
        }
    };
    let bytes = value * multiplier as f64;
    if !bytes.is_finite() || bytes <= 0.0 {
        return Err(Error::invalid_request(format!(
            "byte size '{input}' is not positive"
        )));
    }
    Ok(bytes as usize)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_binary_and_decimal_sizes() {
        assert_eq!(parse_byte_size("1024").unwrap(), 1024);
        assert_eq!(parse_byte_size("4MiB").unwrap(), 4 << 20);
        assert_eq!(parse_byte_size("512KiB").unwrap(), 512 << 10);
        assert_eq!(parse_byte_size("2 GB").unwrap(), 2_000_000_000);
        assert_eq!(parse_byte_size("1m").unwrap(), 1_000_000);
    }

    #[test]
    fn rejects_bad_sizes() {
        assert_eq!(
            parse_byte_size("abc").unwrap_err().kind,
            ErrorKind::InvalidRequest
        );
        assert_eq!(
            parse_byte_size("9XB").unwrap_err().kind,
            ErrorKind::InvalidRequest
        );
        assert_eq!(
            parse_byte_size("0").unwrap_err().kind,
            ErrorKind::InvalidRequest
        );
    }
}
