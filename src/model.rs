//! Core data model: identities, handles, operations and completion records.
//!
//! A `Handle` is the *only* token a caller ever sees. It pairs a slot index
//! with a `Generation` so that a late completion carrying an old generation
//! can never be attributed to a new submission that reused the same slot.

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

/// Logical clock used by the runtime core (milliseconds). The async driver
/// feeds real time; tests feed synthetic time for deterministic timeouts.
pub type Millis = u64;

/// Identity of a single submission record. Bound 1:1 to `user_data` for the
/// lifetime of the submission.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct RecordId(pub u64);

/// Generation counter of a slot. Bumped every time a slot is finalized, so a
/// reused slot always has a different generation than any previous occupant.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Generation(pub u32);

/// Opaque token returned by `submit`. `slot` alone is *not* an identity:
/// only `(slot, generation)` together identify one submission.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Handle {
    pub slot: u16,
    pub generation: Generation,
}

/// The IO operation to perform. `Nop` exists so tests and demos can exercise
/// the queue without touching the filesystem.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum OpKind {
    Nop,
    ReadFile { path: PathBuf },
    WriteFile { path: PathBuf, len: usize },
}

impl OpKind {
    /// Whether this operation needs a data buffer leased from the registry.
    pub fn needs_buffer(&self) -> bool {
        matches!(self, OpKind::ReadFile { .. } | OpKind::WriteFile { .. })
    }

    pub fn name(&self) -> &'static str {
        match self {
            OpKind::Nop => "Nop",
            OpKind::ReadFile { .. } => "ReadFile",
            OpKind::WriteFile { .. } => "WriteFile",
        }
    }
}

/// A caller's request to enqueue one IO operation.
#[derive(Clone, Debug)]
pub struct Submission {
    /// Caller-chosen identity. Bound to exactly one `RecordId`; treated as
    /// sensitive and always redacted in diagnostics.
    pub user_data: u64,
    pub op: OpKind,
    pub timeout_ms: Millis,
}

/// Terminal outcome of a record. Every record reaches exactly one of these.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum CompletionOutcome {
    Success { bytes: usize },
    /// The adapter confirmed the operation was cancelled before it ran.
    Cancelled,
    /// The runtime deadline expired before any adapter event arrived.
    TimedOut,
    /// The adapter reported a failure (e.g. file not found).
    Failed { message: String },
}

impl CompletionOutcome {
    pub fn kind(&self) -> &'static str {
        match self {
            CompletionOutcome::Success { .. } => "Success",
            CompletionOutcome::Cancelled => "Cancelled",
            CompletionOutcome::TimedOut => "TimedOut",
            CompletionOutcome::Failed { .. } => "Failed",
        }
    }
}

/// The single, final completion record of a submission. Written at most once
/// per `RecordId`; this is the unit the journal persists and diagnostics
/// expose (in redacted form).
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct CompletionRecord {
    pub record_id: RecordId,
    pub handle: Handle,
    pub user_data: u64,
    pub op: OpKind,
    /// True if a cancel request was accepted before the record finalized.
    /// Note: `cancel_requested == true` does NOT imply `Cancelled` — the IO
    /// may have completed anyway ("cancel accepted" != "IO did not happen").
    pub cancel_requested: bool,
    pub submitted_at_ms: Millis,
    pub completed_at_ms: Millis,
    pub outcome: CompletionOutcome,
}
