//! Startup configuration: TOML file plus environment overrides.
//!
//! Precedence: built-in defaults < config file < environment variables
//! (`CDC_LISTEN`, `CDC_MAX_BODY_BYTES`, `CDC_MAX_CONCURRENT`).

use serde::Deserialize;
use thiserror::Error;

use crate::limits::Limits;
use crate::params::{ChunkParams, ParamsError};

#[derive(Debug, Clone)]
pub struct ServerConfig {
    pub listen: String,
    pub chunk: ChunkParams,
    pub limits: Limits,
}

#[derive(Debug, Deserialize)]
struct FileConfig {
    listen: Option<String>,
    chunk: Option<FileChunk>,
    limits: Option<FileLimits>,
}

#[derive(Debug, Deserialize)]
struct FileChunk {
    min_size: Option<usize>,
    avg_bits: Option<u32>,
    max_size: Option<usize>,
}

#[derive(Debug, Deserialize)]
struct FileLimits {
    max_body_bytes: Option<usize>,
    max_chunks: Option<usize>,
    max_concurrent: Option<usize>,
    request_timeout_secs: Option<u64>,
}

#[derive(Debug, Error)]
pub enum ConfigError {
    #[error("cannot read config file {path}: {source}")]
    Read { path: String, source: std::io::Error },
    #[error("cannot parse config file {path}: {source}")]
    Parse { path: String, source: toml::de::Error },
    #[error("invalid chunk parameters: {0}")]
    Params(#[from] ParamsError),
}

impl ServerConfig {
    pub fn load(path: Option<&str>) -> Result<Self, ConfigError> {
        let mut cfg = ServerConfig {
            listen: "127.0.0.1:8080".to_string(),
            chunk: ChunkParams::default_params(),
            limits: Limits::default(),
        };
        if let Some(path) = path {
            let text = std::fs::read_to_string(path).map_err(|source| ConfigError::Read {
                path: path.to_string(),
                source,
            })?;
            let file: FileConfig = toml::from_str(&text).map_err(|source| ConfigError::Parse {
                path: path.to_string(),
                source,
            })?;
            cfg.apply_file(file);
        }
        cfg.apply_env();
        cfg.chunk.validate()?;
        Ok(cfg)
    }

    fn apply_file(&mut self, file: FileConfig) {
        if let Some(listen) = file.listen {
            self.listen = listen;
        }
        if let Some(c) = file.chunk {
            if let Some(v) = c.min_size {
                self.chunk.min_size = v;
            }
            if let Some(v) = c.avg_bits {
                self.chunk.avg_bits = v;
            }
            if let Some(v) = c.max_size {
                self.chunk.max_size = v;
            }
        }
        if let Some(l) = file.limits {
            if let Some(v) = l.max_body_bytes {
                self.limits.max_body_bytes = v;
            }
            if let Some(v) = l.max_chunks {
                self.limits.max_chunks = v;
            }
            if let Some(v) = l.max_concurrent {
                self.limits.max_concurrent = v;
            }
            if let Some(v) = l.request_timeout_secs {
                self.limits.request_timeout = std::time::Duration::from_secs(v);
            }
        }
    }

    fn apply_env(&mut self) {
        if let Ok(v) = std::env::var("CDC_LISTEN") {
            self.listen = v;
        }
        if let Ok(v) = std::env::var("CDC_MAX_BODY_BYTES") {
            if let Ok(n) = v.parse() {
                self.limits.max_body_bytes = n;
            }
        }
        if let Ok(v) = std::env::var("CDC_MAX_CONCURRENT") {
            if let Ok(n) = v.parse() {
                self.limits.max_concurrent = n;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn defaults_when_no_file() {
        let cfg = ServerConfig::load(None).unwrap();
        assert_eq!(cfg.listen, "127.0.0.1:8080");
        assert_eq!(cfg.chunk, ChunkParams::default_params());
    }

    #[test]
    fn file_overrides_defaults() {
        let mut f = tempfile();
        writeln!(f, r#"listen = "127.0.0.1:19090""#).unwrap();
        writeln!(f, "[chunk]").unwrap();
        writeln!(f, "min_size = 1024").unwrap();
        writeln!(f, "avg_bits = 12").unwrap();
        writeln!(f, "max_size = 16384").unwrap();
        writeln!(f, "[limits]").unwrap();
        writeln!(f, "max_body_bytes = 1048576").unwrap();
        writeln!(f, "request_timeout_secs = 5").unwrap();
        let cfg = ServerConfig::load(Some(f.path().to_str().unwrap())).unwrap();
        assert_eq!(cfg.listen, "127.0.0.1:19090");
        assert_eq!(cfg.chunk.min_size, 1024);
        assert_eq!(cfg.chunk.avg_bits, 12);
        assert_eq!(cfg.limits.max_body_bytes, 1_048_576);
        assert_eq!(cfg.limits.request_timeout, std::time::Duration::from_secs(5));
    }

    #[test]
    fn invalid_params_fail_fast() {
        let mut f = tempfile();
        writeln!(f, "[chunk]").unwrap();
        writeln!(f, "min_size = 4").unwrap(); // below rolling window
        let err = ServerConfig::load(Some(f.path().to_str().unwrap())).unwrap_err();
        assert!(matches!(err, ConfigError::Params(_)));
    }

    #[test]
    fn missing_file_is_an_error() {
        let err = ServerConfig::load(Some("/nonexistent/cdc.toml")).unwrap_err();
        assert!(matches!(err, ConfigError::Read { .. }));
    }

    /// Minimal temp-file helper (no extra dev-dependency).
    fn tempfile() -> NamedTemp {
        let path = std::env::temp_dir().join(format!(
            "cdc-config-test-{}-{}.toml",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        NamedTemp(std::fs::File::create(&path).unwrap(), path)
    }

    struct NamedTemp(std::fs::File, std::path::PathBuf);

    impl NamedTemp {
        fn path(&self) -> &std::path::Path {
            &self.1
        }
    }

    impl Write for NamedTemp {
        fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
            self.0.write(buf)
        }
        fn flush(&mut self) -> std::io::Result<()> {
            self.0.flush()
        }
    }

    impl Drop for NamedTemp {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.1);
        }
    }
}
