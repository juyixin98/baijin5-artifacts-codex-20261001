//! Public data model: identities, operations, outcomes and the final record.

use serde::{Deserialize, Serialize};

macro_rules! id_type {
    ($name:ident) => {
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
        pub struct $name(pub u64);

        impl std::fmt::Display for $name {
            fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                write!(f, "{}", self.0)
            }
        }
    };
}

id_type!(ConnId);
id_type!(Generation);
id_type!(SubmissionId);
id_type!(BufferId);

/// Identity bound to exactly one submission for its whole lifetime.
///
/// The adapter echoes this token back on completion. Because the connection
/// generation is part of the token, a late completion issued before a
/// connection slot was reused can never claim the slot's new owner.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct UserData {
    pub conn: ConnId,
    pub generation: Generation,
    pub submission: SubmissionId,
}

/// A connection handle as handed out by [`crate::handle::ConnectionTable`].
/// Stale generations are rejected on every use.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct ConnHandle {
    pub conn: ConnId,
    pub generation: Generation,
}

/// A synthetic IO operation. Payload bytes are never stored; a write only
/// carries its length. Buffers are tracked separately by the registry.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum IoOp {
    Read { path: String },
    Write { path: String, len: u32 },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FailureKind {
    Io,
    Adapter,
}

/// The single authoritative outcome of a submission.
///
/// Note that `Cancelled { io_performed: true }` is a first-class outcome:
/// a cancel request can be accepted while the device still reports that the
/// IO physically happened.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum FinalOutcome {
    Success { bytes: u64 },
    Cancelled { io_performed: bool },
    TimedOut,
    Failed { kind: FailureKind },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RecordPhase {
    Submitted,
    InFlight,
    CancelRequested,
    Final,
}

/// The exactly-once final completion record. Once written it is immutable;
/// late completions become orphan diagnostics instead of a second record.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CompletionRecord {
    pub user_data: UserData,
    pub outcome: FinalOutcome,
    pub cancel_requested: bool,
    pub submitted_at_ms: u64,
    pub finished_at_ms: u64,
}

/// Read-only view of a submission for the diagnostic/API surface.
/// Deliberately omits the operation payload (paths stay inside the engine).
#[derive(Debug, Clone, Serialize)]
pub struct RecordView {
    pub user_data: UserData,
    pub phase: RecordPhase,
    pub cancel_requested: bool,
    pub submitted_at_ms: u64,
    pub deadline_ms: u64,
    pub final_record: Option<CompletionRecord>,
}
