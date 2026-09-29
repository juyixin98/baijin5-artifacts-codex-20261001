//! Replay records: every execution gets a run id and a structured record with
//! the input fingerprint, the key intermediate state (checkpoint + stats), the
//! outcome category and a human-readable rationale. Records can be appended to
//! a JSONL file and/or kept in memory, so a failure can be reproduced from its
//! run id alone.

use std::collections::VecDeque;
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use serde::{Deserialize, Serialize};

use crate::error::{ErrorCategory, JoinError};
use crate::operator::plan::JoinPlan;
use crate::operator::Checkpoint;
use crate::resource::{JoinStats, Truncation};
use crate::types::{KeyType, Scalar, TypedBatch};

/// Monotonic, process-unique run identifier (`run-000042`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RunId(String);

static RUN_COUNTER: AtomicU64 = AtomicU64::new(0);

impl RunId {
    pub fn generate() -> Self {
        let n = RUN_COUNTER.fetch_add(1, Ordering::Relaxed) + 1;
        RunId(format!("run-{n:06}"))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl std::fmt::Display for RunId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

/// FNV-1a 64-bit fingerprint over the ordered scalars of one column.
fn fnv1a_scalars(col: &[Scalar]) -> u64 {
    const OFFSET: u64 = 0xcbf29ce484222325;
    const PRIME: u64 = 0x100000001b3;
    fn feed(hash: &mut u64, byte: u8) {
        *hash ^= byte as u64;
        *hash = hash.wrapping_mul(PRIME);
    }
    let mut hash = OFFSET;
    for v in col {
        match v {
            Scalar::Null => {
                feed(&mut hash, 0);
            }
            Scalar::Int(i) => {
                feed(&mut hash, 1);
                for &b in &i.to_le_bytes() {
                    feed(&mut hash, b);
                }
            }
            Scalar::Float(f) => {
                feed(&mut hash, 2);
                for &b in &f.0.to_le_bytes() {
                    feed(&mut hash, b);
                }
            }
            Scalar::Text(t) => {
                feed(&mut hash, 3);
                for b in t.bytes() {
                    feed(&mut hash, b);
                }
                feed(&mut hash, 255);
            }
        }
    }
    hash
}

/// Content fingerprint sufficient to recognize whether a replay uses the same
/// fixture: row counts, column types and a per-column value hash.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct BatchFingerprint {
    pub rows: usize,
    pub columns: Vec<ColumnFingerprint>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ColumnFingerprint {
    pub name: String,
    pub key_type: String,
    pub value_hash: u64,
}

impl BatchFingerprint {
    pub fn of(batch: &TypedBatch) -> Self {
        let columns = batch
            .columns()
            .iter()
            .map(|c| ColumnFingerprint {
                name: c.name().to_string(),
                key_type: c.key_type().as_str().to_string(),
                value_hash: fnv1a_scalars(&c.to_scalars()),
            })
            .collect();
        Self {
            rows: batch.row_count(),
            columns,
        }
    }

    pub fn value_type(&self, idx: usize) -> Option<KeyType> {
        self.columns
            .get(idx)
            .and_then(|c| KeyType::parse(&c.key_type))
    }
}

/// Serializable mirror of the (otherwise opaque) checkpoint — the key
/// resumable intermediate state.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CheckpointSnapshot {
    pub dpos: usize,
    pub act_pos: usize,
    pub probe_pos: usize,
    pub probe_hi: usize,
}

impl From<Checkpoint> for CheckpointSnapshot {
    fn from(c: Checkpoint) -> Self {
        // Checkpoint fields are crate-visible; access via debug conversion is
        // avoided by a dedicated method on Checkpoint.
        let (dpos, act_pos, probe_pos, probe_hi) = c.parts();
        Self {
            dpos,
            act_pos,
            probe_pos,
            probe_hi,
        }
    }
}

/// What happened on a run, in a form a replay harness can assert on.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum OutcomeKind {
    Completed,
    Truncated,
    Failed,
}

/// One structured replay record.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReplayRecord {
    pub run_id: String,
    pub outcome: OutcomeKind,
    pub plan: JoinPlan,
    pub budget: BudgetSnapshot,
    pub left: BatchFingerprint,
    pub right: BatchFingerprint,
    pub checkpoint: CheckpointSnapshot,
    pub stats: JoinStats,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub truncation: Option<Truncation>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<ErrorSnapshot>,
    /// Why the run ended the way it did, in one sentence.
    pub rationale: String,
}

/// Serializable budget mirror.
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct BudgetSnapshot {
    pub max_output: usize,
    pub max_candidate_accesses: u64,
}

impl From<crate::resource::Budget> for BudgetSnapshot {
    fn from(b: crate::resource::Budget) -> Self {
        Self {
            max_output: b.max_output,
            max_candidate_accesses: b.max_candidate_accesses,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ErrorSnapshot {
    pub code: String,
    pub category: String,
    pub message: String,
}

impl ErrorSnapshot {
    pub fn from_error(e: &JoinError) -> Self {
        let category = match e.category() {
            ErrorCategory::Input => "input",
            ErrorCategory::State => "state",
            ErrorCategory::Resource => "resource",
            ErrorCategory::Compute => "compute",
        };
        Self {
            code: e.code.as_str().to_string(),
            category: category.to_string(),
            message: e.message.clone(),
        }
    }
}

/// Where records go: an optional JSONL append-file plus a bounded in-memory
/// ring (the ring is what tests inspect).
#[derive(Clone)]
pub struct ReplayLogger {
    file: Arc<Mutex<Option<File>>>,
    ring: Arc<Mutex<VecDeque<ReplayRecord>>>,
    ring_capacity: usize,
}

impl ReplayLogger {
    /// Logger that keeps the last `ring_capacity` records in memory only.
    pub fn memory(ring_capacity: usize) -> Self {
        Self {
            file: Arc::new(Mutex::new(None)),
            ring: Arc::new(Mutex::new(VecDeque::new())),
            ring_capacity,
        }
    }

    /// Logger that additionally appends every record as one JSON line.
    pub fn with_file(ring_capacity: usize, path: &Path) -> std::io::Result<Self> {
        let file = OpenOptions::new().create(true).append(true).open(path)?;
        Ok(Self {
            file: Arc::new(Mutex::new(Some(file))),
            ring: Arc::new(Mutex::new(VecDeque::new())),
            ring_capacity,
        })
    }

    pub fn record(&self, rec: ReplayRecord) -> String {
        {
            let mut ring = self.ring.lock().expect("replay ring poisoned");
            if ring.len() >= self.ring_capacity {
                ring.pop_front();
            }
            ring.push_back(rec.clone());
        }
        if let Some(file) = self.file.lock().expect("replay file poisoned").as_mut() {
            let line = serde_json::to_string(&rec).unwrap_or_else(|_| "{}".to_string());
            let _ = writeln!(file, "{line}");
        }
        rec.run_id
    }

    /// Look up a record by run id (most recent match first).
    pub fn get(&self, run_id: &str) -> Option<ReplayRecord> {
        let ring = self.ring.lock().expect("replay ring poisoned");
        ring.iter().rev().find(|r| r.run_id == run_id).cloned()
    }

    pub fn all(&self) -> Vec<ReplayRecord> {
        self.ring
            .lock()
            .expect("replay ring poisoned")
            .iter()
            .cloned()
            .collect()
    }
}
