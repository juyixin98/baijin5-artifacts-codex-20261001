//! Persistent run store: one JSON object per line (JSONL), append-only.
//!
//! Deliberately simple and inspectable: `data/runs.jsonl` can be tailed,
//! grepped and diffed without any tooling beyond a text editor.

use crate::error::{FailureCategory, MergeError};
use crate::model::MergeRun;
use std::fs::{self, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

pub struct RunStore {
    path: PathBuf,
    /// Serializes appends; readers take the lock too so they never observe
    /// a partially written line.
    lock: Mutex<()>,
}

impl RunStore {
    pub fn open(path: &Path) -> Result<Self, MergeError> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        // Touch the file so readers never hit ENOENT.
        OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)?;
        Ok(Self {
            path: path.to_path_buf(),
            lock: Mutex::new(()),
        })
    }

    pub fn append(&self, run: &MergeRun) -> Result<(), MergeError> {
        let line = serde_json::to_string(run)
            .map_err(|e| MergeError::new(FailureCategory::Io, format!("serialize run: {e}")))?;
        let _guard = self.lock.lock().map_err(|_| {
            MergeError::new(FailureCategory::Io, "store lock poisoned".to_string())
        })?;
        let mut f = OpenOptions::new().append(true).open(&self.path)?;
        f.write_all(line.as_bytes())?;
        f.write_all(b"\n")?;
        Ok(())
    }

    pub fn get(&self, run_id: &str) -> Result<Option<MergeRun>, MergeError> {
        Ok(self
            .all()?
            .into_iter()
            .rev()
            .find(|r| r.run_id == run_id))
    }

    pub fn list(&self) -> Result<Vec<MergeRun>, MergeError> {
        self.all()
    }

    fn all(&self) -> Result<Vec<MergeRun>, MergeError> {
        let _guard = self.lock.lock().map_err(|_| {
            MergeError::new(FailureCategory::Io, "store lock poisoned".to_string())
        })?;
        let f = fs::File::open(&self.path)?;
        let mut runs = Vec::new();
        for line in BufReader::new(f).lines() {
            let line = line?;
            if line.trim().is_empty() {
                continue;
            }
            let run: MergeRun = serde_json::from_str(&line).map_err(|e| {
                MergeError::new(
                    FailureCategory::Io,
                    format!("corrupt store line in {}: {e}", self.path.display()),
                )
            })?;
            runs.push(run);
        }
        Ok(runs)
    }
}
