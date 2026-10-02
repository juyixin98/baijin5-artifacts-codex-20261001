//! Persistent and sampled state.
//!
//! Every final completion record is appended to a JSONL journal (persistent
//! state). Every `snapshot_every` completions a sampled snapshot of the
//! engine statistics is appended as well (sampled state). The sink is
//! pluggable: a file for the server, memory for tests.

use serde::Serialize;

use crate::engine::Stats;
use crate::model::CompletionRecord;

pub trait JournalSink: Send {
    fn append(&mut self, line: &str) -> std::io::Result<()>;
    /// In-memory sinks expose their lines for independent verification.
    fn lines(&self) -> Option<&[String]> {
        None
    }
}

/// Append-only JSONL file journal. Each line is flushed immediately
/// (best-effort durability; no fsync — see README limitations).
pub struct FileJournal {
    file: std::fs::File,
}

impl FileJournal {
    pub fn create(path: &std::path::Path) -> std::io::Result<Self> {
        if let Some(parent) = path.parent() {
            if !parent.as_os_str().is_empty() {
                std::fs::create_dir_all(parent)?;
            }
        }
        Ok(Self {
            file: std::fs::File::create(path)?,
        })
    }
}

impl JournalSink for FileJournal {
    fn append(&mut self, line: &str) -> std::io::Result<()> {
        use std::io::Write;
        self.file.write_all(line.as_bytes())?;
        self.file.write_all(b"\n")?;
        self.file.flush()
    }
}

#[derive(Debug, Default)]
pub struct MemJournal {
    lines: Vec<String>,
}

impl MemJournal {
    pub fn new() -> Self {
        Self::default()
    }
}

impl JournalSink for MemJournal {
    fn append(&mut self, line: &str) -> std::io::Result<()> {
        self.lines.push(line.to_string());
        Ok(())
    }

    fn lines(&self) -> Option<&[String]> {
        Some(&self.lines)
    }
}

#[derive(Debug, Serialize)]
struct JournalEntry<'a, T: Serialize> {
    #[serde(rename = "type")]
    kind: &'static str,
    payload: &'a T,
}

pub struct Journal {
    sink: Box<dyn JournalSink>,
    snapshot_every: u64,
    completions_since_snapshot: u64,
    total_completions: u64,
}

impl Journal {
    pub fn new(sink: Box<dyn JournalSink>, snapshot_every: u64) -> Self {
        Self {
            sink,
            snapshot_every: snapshot_every.max(1),
            completions_since_snapshot: 0,
            total_completions: 0,
        }
    }

    pub fn record_completion(&mut self, record: &CompletionRecord) {
        let line = serde_json::to_string(&JournalEntry {
            kind: "completion",
            payload: record,
        })
        .expect("CompletionRecord serializes");
        // A journal that cannot persist must not silently drop records.
        self.sink.append(&line).expect("journal append failed");
        self.total_completions += 1;
        self.completions_since_snapshot += 1;
    }

    /// Append a sampled snapshot when the interval has elapsed.
    pub fn maybe_snapshot(&mut self, stats: &Stats) {
        if self.completions_since_snapshot < self.snapshot_every {
            return;
        }
        self.completions_since_snapshot = 0;
        let line = serde_json::to_string(&JournalEntry {
            kind: "snapshot",
            payload: stats,
        })
        .expect("Stats serializes");
        self.sink.append(&line).expect("journal append failed");
    }

    pub fn total_completions(&self) -> u64 {
        self.total_completions
    }

    pub fn lines(&self) -> Option<&[String]> {
        self.sink.lines()
    }
}
