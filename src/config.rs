//! Configuration: TOML file plus `MERGE_CHECK_*` environment overrides.

use serde::Deserialize;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone)]
pub struct AppConfig {
    pub bind: String,
    pub state_dir: PathBuf,
    /// Allowlist of directories layer paths must resolve under.
    pub layer_roots: Vec<PathBuf>,
    pub max_entries: usize,
    pub max_symlink_depth: usize,
}

#[derive(Debug, Deserialize)]
#[serde(default)]
struct FileConfig {
    bind: String,
    state_dir: PathBuf,
    layer_roots: Vec<PathBuf>,
    max_entries: usize,
    max_symlink_depth: usize,
}

impl Default for FileConfig {
    fn default() -> Self {
        Self {
            bind: "127.0.0.1:8080".to_string(),
            state_dir: PathBuf::from("./state"),
            layer_roots: vec![PathBuf::from("./fixtures")],
            max_entries: 100_000,
            max_symlink_depth: 8,
        }
    }
}

#[derive(Debug)]
pub enum ConfigError {
    Io(std::io::Error),
    Parse(toml::de::Error),
    LayerRootNotFound(PathBuf),
}

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ConfigError::Io(e) => write!(f, "config I/O error: {e}"),
            ConfigError::Parse(e) => write!(f, "config parse error: {e}"),
            ConfigError::LayerRootNotFound(p) => {
                write!(f, "configured layer root does not exist: {}", p.display())
            }
        }
    }
}

impl std::error::Error for ConfigError {}

impl AppConfig {
    /// Load from a TOML file (missing file = defaults), then apply
    /// `MERGE_CHECK_*` environment overrides. Layer roots are
    /// canonicalized so request paths can be prefix-checked safely.
    pub fn load(path: &Path) -> Result<Self, ConfigError> {
        let mut cfg: FileConfig = match std::fs::read_to_string(path) {
            Ok(text) => toml::from_str(&text).map_err(ConfigError::Parse)?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => FileConfig::default(),
            Err(e) => return Err(ConfigError::Io(e)),
        };

        if let Ok(v) = std::env::var("MERGE_CHECK_BIND") {
            cfg.bind = v;
        }
        if let Ok(v) = std::env::var("MERGE_CHECK_STATE_DIR") {
            cfg.state_dir = PathBuf::from(v);
        }
        if let Ok(v) = std::env::var("MERGE_CHECK_LAYER_ROOTS") {
            cfg.layer_roots = v.split(':').map(PathBuf::from).collect();
        }
        if let Ok(v) = std::env::var("MERGE_CHECK_MAX_ENTRIES") {
            if let Ok(n) = v.parse() {
                cfg.max_entries = n;
            }
        }

        let mut roots = Vec::with_capacity(cfg.layer_roots.len());
        for root in &cfg.layer_roots {
            let canon = root
                .canonicalize()
                .map_err(|_| ConfigError::LayerRootNotFound(root.clone()))?;
            roots.push(canon);
        }

        Ok(Self {
            bind: cfg.bind,
            state_dir: cfg.state_dir,
            layer_roots: roots,
            max_entries: cfg.max_entries,
            max_symlink_depth: cfg.max_symlink_depth,
        })
    }

    /// True when `path` canonicalizes to a location inside one of the
    /// configured layer roots.
    pub fn layer_path_allowed(&self, path: &Path) -> bool {
        let Ok(canon) = path.canonicalize() else {
            return false;
        };
        self.layer_roots.iter().any(|root| canon.starts_with(root))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_apply_when_file_missing() {
        let cfg = AppConfig::load(Path::new("/no/such/config.toml")).unwrap();
        assert_eq!(cfg.bind, "127.0.0.1:8080");
        assert_eq!(cfg.max_symlink_depth, 8);
    }

    #[test]
    fn layer_path_allowlist() {
        let tmp = tempfile::tempdir().unwrap();
        let inside = tmp.path().join("layers/l1");
        std::fs::create_dir_all(&inside).unwrap();
        let cfg = AppConfig {
            bind: String::new(),
            state_dir: tmp.path().to_path_buf(),
            layer_roots: vec![tmp.path().join("layers").canonicalize().unwrap()],
            max_entries: 100,
            max_symlink_depth: 8,
        };
        assert!(cfg.layer_path_allowed(&inside));
        assert!(!cfg.layer_path_allowed(Path::new("/etc")));
        assert!(!cfg.layer_path_allowed(Path::new("/definitely/missing")));
    }
}
