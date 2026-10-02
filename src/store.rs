//! Persistence of sampling state.
//!
//! State is written as JSON to `<data_dir>/state.json` via a write-to-temp +
//! atomic rename, so a crash mid-write never leaves a half-written state
//! file. Loading a missing file yields `None` (fresh start); a corrupt file
//! is an explicit error rather than a silent reset.

use crate::engine::EngineState;
use std::path::PathBuf;

#[derive(Debug, thiserror::Error)]
pub enum StoreError {
    #[error("cannot create data dir {path}: {source}")]
    CreateDir {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("cannot write state file {path}: {source}")]
    Write {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("cannot read state file {path}: {source}")]
    Read {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("cannot parse state file {path}: {source}")]
    Parse {
        path: PathBuf,
        #[source]
        source: serde_json::Error,
    },
}

#[derive(Clone, Debug)]
pub struct Store {
    dir: PathBuf,
}

impl Store {
    pub fn new(dir: PathBuf) -> Self {
        Self { dir }
    }

    pub fn state_path(&self) -> PathBuf {
        self.dir.join("state.json")
    }

    pub fn load(&self) -> Result<Option<EngineState>, StoreError> {
        let path = self.state_path();
        let text = match std::fs::read_to_string(&path) {
            Ok(text) => text,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(source) => return Err(StoreError::Read { path, source }),
        };
        let state = serde_json::from_str(&text).map_err(|source| StoreError::Parse {
            path: path.clone(),
            source,
        })?;
        Ok(Some(state))
    }

    pub fn save(&self, state: &EngineState) -> Result<(), StoreError> {
        std::fs::create_dir_all(&self.dir).map_err(|source| StoreError::CreateDir {
            path: self.dir.clone(),
            source,
        })?;
        let tmp = self.dir.join("state.json.tmp");
        let data = serde_json::to_vec_pretty(state).expect("EngineState is serializable");
        std::fs::write(&tmp, data).map_err(|source| StoreError::Write {
            path: tmp.clone(),
            source,
        })?;
        std::fs::rename(&tmp, self.state_path()).map_err(|source| StoreError::Write {
            path: self.state_path(),
            source,
        })?;
        Ok(())
    }
}
