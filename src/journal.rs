//! Journal: the persistent, sampled state of the runtime.
//!
//! Every terminal [`CompletionRecord`] and every [`Anomaly`] is appended to
//! a JSONL file. Finalized records are sampled (`sample_n`: keep 1 of n) so
//! the journal stays small under load; anomalies are *always* persisted
//! because they indicate contract violations that must not be lost.
//!
//! The journal is written by the driver, not the core, keeping persistence
//! out of the run model.

use crate::model::CompletionRecord;
use crate::runtime::Anomaly;
use serde::Serialize;
use std::fs::{self, File, OpenOptions};
use std::io::{self, BufRead, BufReader, Write};
use std::path::{Path, PathBuf};

/// One line in the journal file.
#[derive(Clone, Debug, Serialize)]
#[serde(tag = "type")]
pub enum JournalEntry<'a> {
    Finalized { record: &'a CompletionRecord },
    Anomaly { anomaly: &'a Anomaly },
}

pub struct Journal {
    path: PathBuf,
    file: File,
    sample_n: u32,
    finalized_seen: u64,
    finalized_written: u64,
    anomalies_written: u64,
}

impl Journal {
    /// Open (creating if needed) the journal at `path` in append mode.
    pub fn open(path: impl AsRef<Path>, sample_n: u32) -> io::Result<Self> {
        let path = path.as_ref().to_path_buf();
        if let Some(parent) = path.parent() {
            if !parent.as_os_str().is_empty() {
                fs::create_dir_all(parent)?;
            }
        }
        let file = OpenOptions::new().create(true).append(true).open(&path)?;
        Ok(Self {
            path,
            file,
            sample_n: sample_n.max(1),
            finalized_seen: 0,
            finalized_written: 0,
            anomalies_written: 0,
        })
    }

    /// In-memory journal for tests that only care about sampling decisions.
    pub fn null(sample_n: u32) -> Self {
        Self::open(std::env::temp_dir().join(format!(
            "io-queue-runtime-null-journal-{}-{}.jsonl",
            std::process::id(),
            std::thread::current().name().unwrap_or("t")
        )), sample_n)
        .expect("temp journal opens")
    }

    /// Persist a finalized record, subject to sampling.
    /// Returns true if the record was actually written.
    pub fn append_finalized(&mut self, record: &CompletionRecord) -> io::Result<bool> {
        self.finalized_seen += 1;
        // Deterministic sampling: keep the 1st of every n records.
        if (self.finalized_seen - 1) % u64::from(self.sample_n) != 0 {
            return Ok(false);
        }
        self.write_line(&JournalEntry::Finalized { record })?;
        self.finalized_written += 1;
        Ok(true)
    }

    /// Persist an anomaly. Anomalies are never sampled away.
    pub fn append_anomaly(&mut self, anomaly: &Anomaly) -> io::Result<()> {
        self.write_line(&JournalEntry::Anomaly { anomaly })?;
        self.anomalies_written += 1;
        Ok(())
    }

    fn write_line(&mut self, entry: &JournalEntry<'_>) -> io::Result<()> {
        let line = serde_json::to_string(entry)
            .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;
        self.file.write_all(line.as_bytes())?;
        self.file.write_all(b"\n")?;
        self.file.flush()
    }

    pub fn finalized_seen(&self) -> u64 {
        self.finalized_seen
    }

    pub fn finalized_written(&self) -> u64 {
        self.finalized_written
    }

    pub fn anomalies_written(&self) -> u64 {
        self.anomalies_written
    }

    pub fn path(&self) -> &Path {
        &self.path
    }
}

/// Read the last `limit` lines of a journal file. Missing file yields an
/// empty list (diagnostics must not fail because nothing was journaled yet).
pub fn read_tail(path: &Path, limit: usize) -> Vec<serde_json::Value> {
    let file = match File::open(path) {
        Ok(f) => f,
        Err(_) => return Vec::new(),
    };
    let lines: Vec<String> = BufReader::new(file)
        .lines()
        .map_while(Result::ok)
        .collect();
    lines
        .iter()
        .rev()
        .take(limit)
        .filter_map(|l| serde_json::from_str(l).ok())
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{CompletionOutcome, Generation, Handle, OpKind, RecordId};

    fn record(id: u64) -> CompletionRecord {
        CompletionRecord {
            record_id: RecordId(id),
            handle: Handle {
                slot: 0,
                generation: Generation(0),
            },
            user_data: 42,
            op: OpKind::Nop,
            cancel_requested: false,
            submitted_at_ms: 0,
            completed_at_ms: 1,
            outcome: CompletionOutcome::Success { bytes: 0 },
        }
    }

    #[test]
    fn sampling_keeps_one_of_n() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("journal.jsonl");
        let mut journal = Journal::open(&path, 3).expect("open");
        for id in 1..=7 {
            journal.append_finalized(&record(id)).expect("append");
        }
        assert_eq!(journal.finalized_seen(), 7);
        assert_eq!(journal.finalized_written(), 3, "records 1, 4, 7 kept");
        let tail = read_tail(&path, 100);
        assert_eq!(tail.len(), 3);
    }

    #[test]
    fn anomalies_are_never_sampled() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("journal.jsonl");
        let mut journal = Journal::open(&path, 1000).expect("open");
        for slot in 0..5u16 {
            journal
                .append_anomaly(&Anomaly::CompletionForIdleSlot {
                    slot,
                    event_generation: 0,
                })
                .expect("append");
        }
        assert_eq!(journal.anomalies_written(), 5);
        assert_eq!(read_tail(&path, 100).len(), 5);
    }
}
