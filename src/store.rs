//! Persistence: snapshots are appended to a JSONL journal; engine state is
//! rebuilt by replaying the journal, so there is a single source of truth.

use std::fs::{self, OpenOptions};
use std::io::{self, BufRead, BufReader, Write};
use std::path::{Path, PathBuf};

use crate::engine::{Engine, EngineConfig, IngestOutcome};
use crate::model::Snapshot;

const JOURNAL: &str = "snapshots.jsonl";

pub struct Store {
    dir: PathBuf,
}

impl Store {
    pub fn open(dir: &Path) -> io::Result<Store> {
        fs::create_dir_all(dir)?;
        Ok(Store { dir: dir.to_path_buf() })
    }

    /// Append one snapshot to the journal. Called only after the engine
    /// accepted the snapshot, so the journal never contains rejected input.
    pub fn append(&self, snap: &Snapshot) -> io::Result<()> {
        let mut f = OpenOptions::new().create(true).append(true).open(self.dir.join(JOURNAL))?;
        let line = serde_json::to_string(snap).map_err(io::Error::other)?;
        f.write_all(line.as_bytes())?;
        f.write_all(b"\n")
    }

    pub fn load_all(&self) -> io::Result<Vec<Snapshot>> {
        let path = self.dir.join(JOURNAL);
        if !path.exists() {
            return Ok(Vec::new());
        }
        let reader = BufReader::new(fs::File::open(&path)?);
        let mut out = Vec::new();
        for (n, line) in reader.lines().enumerate() {
            let line = line?;
            if line.trim().is_empty() {
                continue;
            }
            let snap: Snapshot = serde_json::from_str(&line).map_err(|e| {
                io::Error::new(io::ErrorKind::InvalidData, format!("{}:{}: {e}", path.display(), n + 1))
            })?;
            out.push(snap);
        }
        Ok(out)
    }

    /// Rebuild an engine by replaying the journal. Aborts on the first
    /// rejection — a journal written by `append` can never reject, so a
    /// rejection here means the journal was tampered with.
    pub fn replay(&self, cfg: EngineConfig) -> io::Result<(Engine, Vec<IngestOutcome>)> {
        let mut engine = Engine::new(cfg);
        let mut outcomes = Vec::new();
        for snap in self.load_all()? {
            let outcome = engine.apply(&snap);
            if let IngestOutcome::Rejected(reason) = &outcome {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    format!("journal replay rejected seq {}: {reason:?}", snap.meta.seq),
                ));
            }
            outcomes.push(outcome);
        }
        Ok((engine, outcomes))
    }
}
