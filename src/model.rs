//! Run model: request/response types for a merge run, the final tree entries,
//! provenance, and the diagnostic vocabulary shared by engine, store and API.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Schema version stamped into every persisted report. Bump when the
/// serialized shape of [`MergeReport`] changes incompatibly.
pub const REPORT_SCHEMA_VERSION: u32 = 1;

/// Engine version reported via the API and embedded in reports.
pub const ENGINE_VERSION: &str = env!("CARGO_PKG_VERSION");

/// A layer participating in a merge, lowest first.
#[derive(Debug, Clone)]
pub struct LayerInput {
    /// Caller-supplied identifier (used in provenance and logs).
    pub id: String,
    /// Filesystem path of the unpacked layer directory.
    pub root: std::path::PathBuf,
}

/// What kind of node a final-tree entry is.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum EntryKind {
    File,
    Dir,
    Symlink,
}

/// Resolution status of a symlink entry after the post-merge link pass.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LinkStatus {
    /// Relative target that resolves to an existing entry inside the root.
    Internal,
    /// Relative target that stays inside the root but names no entry.
    /// Kept in the tree; reported as an uncertainty.
    Dangling,
}

/// One node of the merged final tree, with provenance.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FinalEntry {
    /// Normalized layer-relative path, `/`-separated, no leading slash.
    pub path: String,
    pub kind: EntryKind,
    /// Index (0 = lowest) of the layer this entry was taken from.
    pub source_layer: usize,
    /// Caller-supplied id of the source layer.
    pub layer_id: String,
    /// Regular files only: byte size.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub size: Option<u64>,
    /// Regular files only: lowercase hex SHA-256 of the content.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sha256: Option<String>,
    /// Symlinks only: the raw target as stored.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub link_target: Option<String>,
    /// Symlinks only: resolution status.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub link_status: Option<LinkStatus>,
}

/// Failure/uncertainty categories. Stable machine-readable vocabulary;
/// tests assert on these, so extend rather than rename.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DiagnosticCategory {
    /// Symlink whose relative target escapes the isolation root.
    EscapesRoot,
    /// Symlink with an absolute target (out of supported scope).
    AbsoluteTarget,
    /// Symlink chain exceeding the depth limit (cycle or abuse).
    LinkLoop,
    /// Layer node that is not a file/dir/symlink (fifo, socket, device).
    UnsupportedType,
    /// Layer name bytes that are not valid UTF-8.
    NonUtf8Name,
    /// Layer directory missing or unreadable.
    LayerUnavailable,
    /// Internal inconsistency while applying a layer (defensive).
    InconsistentLayer,
    /// Symlink whose target names no entry in the final tree.
    DanglingLink,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Severity {
    /// Entry excluded from the final tree (or the run aborted).
    Failure,
    /// Entry kept, but the conclusion is not certain.
    Uncertain,
}

/// One explainable diagnostic record.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Diagnostic {
    pub severity: Severity,
    pub category: DiagnosticCategory,
    /// Layer index the diagnostic belongs to, when applicable.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub layer: Option<usize>,
    /// Layer-relative path the diagnostic is about, when applicable.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
    pub message: String,
}

impl Diagnostic {
    pub fn failure(
        category: DiagnosticCategory,
        layer: Option<usize>,
        path: Option<String>,
        message: impl Into<String>,
    ) -> Self {
        Self {
            severity: Severity::Failure,
            category,
            layer,
            path,
            message: message.into(),
        }
    }

    pub fn uncertain(
        category: DiagnosticCategory,
        layer: Option<usize>,
        path: Option<String>,
        message: impl Into<String>,
    ) -> Self {
        Self {
            severity: Severity::Uncertain,
            category,
            layer,
            path,
            message: message.into(),
        }
    }
}

/// Per-layer accounting recorded in the report.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LayerDescriptor {
    pub index: usize,
    pub id: String,
    pub root: String,
    pub entries_scanned: usize,
    pub whiteouts_applied: usize,
    pub opaque_dirs_applied: usize,
}

/// Aggregate counters for the run.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct MergeStats {
    pub files: usize,
    pub dirs: usize,
    pub symlinks: usize,
    pub total_file_bytes: u64,
    pub entries_replaced: usize,
    pub entries_removed_by_whiteout: usize,
    pub entries_removed_by_opaque: usize,
}

/// The persisted, explainable result of one merge run.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MergeReport {
    pub schema_version: u32,
    pub engine_version: String,
    /// Server-generated unique id of this run (store key).
    pub run_id: String,
    /// Caller-correlated id (header or body), echoed for log correlation.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
    pub started_at: String,
    pub finished_at: String,
    pub layers: Vec<LayerDescriptor>,
    /// Final tree, keyed by normalized path, ordered by path.
    pub entries: BTreeMap<String, FinalEntry>,
    /// Hard failures, listed separately from uncertainties.
    pub failures: Vec<Diagnostic>,
    /// Uncertain conclusions, listed separately from failures.
    pub uncertainties: Vec<Diagnostic>,
    pub stats: MergeStats,
}

/// Inbound merge request body.
#[derive(Debug, Clone, Deserialize)]
pub struct MergeRequest {
    /// Optional caller correlation id; echoed into logs and the report.
    #[serde(default)]
    pub request_id: Option<String>,
    /// Layer directories, lowest first. Each may be a string path or an
    /// object `{ "id": ..., "path": ... }`.
    pub layers: Vec<LayerSpec>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(untagged)]
pub enum LayerSpec {
    Path(String),
    Named { id: String, path: String },
}
