//! Run model: the persistent record of one merge run.
//!
//! A `MergeRun` captures everything needed to explain a run afterwards:
//! who asked (request id), what was processed (layers, steps), what came out
//! (entries with per-item provenance), and what went wrong or stayed
//! undecided (failures and uncertainties, listed separately).

use crate::error::FailureCategory;
use serde::{Deserialize, Serialize};

/// Tool version stamped onto every run, from Cargo.toml.
pub const TOOL_VERSION: &str = env!("CARGO_PKG_VERSION");

/// Reference to one input layer, by merge order index and path.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LayerRef {
    pub index: usize,
    pub path: String,
}

/// Kind of a final-tree entry.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EntryKind {
    File,
    Dir,
    Symlink,
}

/// One entry of the produced final tree, with provenance.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EntryRecord {
    /// Root-relative path, `/`-separated, no leading slash.
    pub path: String,
    pub kind: EntryKind,
    /// Layer this entry (as it appears in the final tree) originates from.
    pub source: LayerRef,
    /// sha256 hex digest, files only.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sha256: Option<String>,
    /// Size in bytes, files only.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub size: Option<u64>,
    /// Link target as stored, symlinks only.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub link_target: Option<String>,
}

/// A per-entry processing failure. The run continues past these.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FailureRecord {
    pub category: FailureCategory,
    /// Root-relative path of the offending entry, when applicable.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub layer: Option<LayerRef>,
    pub message: String,
}

/// Why a conclusion could not be drawn for an entry.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UncertaintyKind {
    /// Symlink skipped because its target leaves the merge root; we do not
    /// guess what it would have pointed at.
    SymlinkTargetOutsideRoot,
    /// Absolute symlink targets are outside the supported link scope.
    AbsoluteSymlinkUnsupported,
}

/// An entry for which no definitive conclusion was produced.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UncertaintyRecord {
    pub reason: UncertaintyKind,
    pub path: String,
    pub layer: LayerRef,
    pub message: String,
}

/// One key processing step, for the diagnostic timeline.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StepRecord {
    pub seq: u32,
    pub name: String,
    pub detail: String,
}

/// Final status of a run.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RunStatus {
    /// All entries processed, no failures, no uncertainties.
    Success,
    /// Final tree produced, but some entries have no definitive conclusion.
    WithUncertainties,
    /// Final tree produced, but some entries failed to process.
    WithFailures,
    /// Both failures and uncertainties occurred.
    WithFailuresAndUncertainties,
    /// The run itself could not complete (fatal, e.g. missing layer).
    Failed,
}

impl RunStatus {
    pub fn from_counts(failures: usize, uncertainties: usize) -> Self {
        match (failures > 0, uncertainties > 0) {
            (false, false) => RunStatus::Success,
            (false, true) => RunStatus::WithUncertainties,
            (true, false) => RunStatus::WithFailures,
            (true, true) => RunStatus::WithFailuresAndUncertainties,
        }
    }
}

/// The full, persistable record of one merge run.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MergeRun {
    pub run_id: String,
    /// Correlates with the caller's `x-request-id` (or a generated one).
    pub request_id: String,
    pub tool_version: String,
    pub started_at: String,
    pub finished_at: String,
    pub status: RunStatus,
    pub layers: Vec<LayerRef>,
    pub output_dir: String,
    pub entries: Vec<EntryRecord>,
    pub failures: Vec<FailureRecord>,
    pub uncertainties: Vec<UncertaintyRecord>,
    pub steps: Vec<StepRecord>,
}

impl MergeRun {
    /// Look up the final-tree entry at a root-relative path.
    pub fn entry(&self, path: &str) -> Option<&EntryRecord> {
        self.entries.iter().find(|e| e.path == path)
    }
}
