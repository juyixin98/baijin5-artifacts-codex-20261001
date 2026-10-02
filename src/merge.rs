//! Resource algorithm: the overlay merge engine.
//!
//! Semantics (fixed, tested against hand-written expected trees):
//! - Layers apply lowest-first, in the caller's order.
//! - Within one layer the apply order is fixed: (1) opaque markers,
//!   (2) whiteouts, (3) regular entries (parents before children).
//!   This makes "delete then recreate in the same layer" deterministic:
//!   the whiteout clears lower content, the same-layer entry then wins.
//! - A whiteout `.wh.<name>` removes sibling `<name>` (file or dir
//!   subtree) from the merged-so-far state.
//! - An opaque marker `.wh..wh..opq` removes all merged-so-far
//!   *children* of its directory; the directory itself survives.
//! - A regular entry replaces any existing entry at its path; replacing
//!   a directory with a non-directory drops the whole subtree
//!   (file/dir swap in either direction).
//! - Symlinks are recorded, never followed during the scan. A post-pass
//!   validates targets: absolute targets and targets escaping the root
//!   are failures and the entry is excluded; chains deeper than
//!   `max_symlink_depth` are failures; missing targets are kept but
//!   reported as uncertainties.

use crate::model::*;
use crate::paths::{self, MarkerKind, NormalizeError};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fmt;
use std::io;
use std::path::{Path, PathBuf};
use walkdir::WalkDir;

/// Tunables for the engine, supplied by configuration.
#[derive(Debug, Clone)]
pub struct EngineOptions {
    /// Maximum symlink chain length before declaring a loop.
    pub max_symlink_depth: usize,
    /// Safety cap on total scanned entries across all layers.
    pub max_entries: usize,
}

impl Default for EngineOptions {
    fn default() -> Self {
        Self {
            max_symlink_depth: 8,
            max_entries: 100_000,
        }
    }
}

/// Run-aborting error (per-entry problems are diagnostics, not errors).
#[derive(Debug)]
pub enum MergeError {
    LayerUnavailable { layer: usize, path: PathBuf },
    Io { layer: usize, source: io::Error },
    EntryLimitExceeded { limit: usize },
}

impl fmt::Display for MergeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            MergeError::LayerUnavailable { layer, path } => write!(
                f,
                "layer {layer} directory is missing or not a directory: {}",
                path.display()
            ),
            MergeError::Io { layer, source } => {
                write!(f, "I/O error while scanning layer {layer}: {source}")
            }
            MergeError::EntryLimitExceeded { limit } => {
                write!(f, "entry limit exceeded ({limit})")
            }
        }
    }
}

impl std::error::Error for MergeError {}

/// The merge engine: pure function of layer directories to a report body.
pub struct MergeEngine {
    opts: EngineOptions,
}

/// Intermediate scanned node, before application.
struct Scanned {
    rel: PathBuf,
    kind: ScannedKind,
}

enum ScannedKind {
    File { size: u64, sha256: String },
    Dir,
    Symlink { target: String },
}

/// Result of the engine, before the API layer wraps it with run metadata.
#[derive(Debug)]
pub struct EngineOutput {
    pub layers: Vec<LayerDescriptor>,
    pub entries: BTreeMap<String, FinalEntry>,
    pub failures: Vec<Diagnostic>,
    pub uncertainties: Vec<Diagnostic>,
    pub stats: MergeStats,
}

impl MergeEngine {
    pub fn new(opts: EngineOptions) -> Self {
        Self { opts }
    }

    /// Merge `layers` (lowest first) into a final content tree.
    pub fn merge(&self, layers: &[LayerInput]) -> Result<EngineOutput, MergeError> {
        let mut tree: BTreeMap<String, FinalEntry> = BTreeMap::new();
        let mut descriptors = Vec::with_capacity(layers.len());
        let mut failures: Vec<Diagnostic> = Vec::new();
        let mut uncertainties: Vec<Diagnostic> = Vec::new();
        let mut stats = MergeStats::default();
        let mut total_scanned = 0usize;

        for (index, layer) in layers.iter().enumerate() {
            if !layer.root.is_dir() {
                failures.push(Diagnostic::failure(
                    DiagnosticCategory::LayerUnavailable,
                    Some(index),
                    None,
                    format!("layer directory unavailable: {}", layer.root.display()),
                ));
                return Err(MergeError::LayerUnavailable {
                    layer: index,
                    path: layer.root.clone(),
                });
            }
            let scanned = scan_layer(index, layer, &mut failures, &mut total_scanned, self.opts.max_entries)?;
            let (mut descriptor, applied) =
                self.apply_layer(index, &layer.id, scanned, &mut tree, &mut stats);
            descriptor.root = layer.root.display().to_string();
            tracing::info!(
                layer = index,
                layer_id = %layer.id,
                scanned = descriptor.entries_scanned,
                whiteouts = descriptor.whiteouts_applied,
                opaque = descriptor.opaque_dirs_applied,
                "layer applied"
            );
            descriptors.push(descriptor);
            uncertainties.extend(applied);
        }

        self.validate_links(&mut tree, &mut failures, &mut uncertainties);

        for entry in tree.values() {
            match entry.kind {
                EntryKind::File => {
                    stats.files += 1;
                    stats.total_file_bytes += entry.size.unwrap_or(0);
                }
                EntryKind::Dir => stats.dirs += 1,
                EntryKind::Symlink => stats.symlinks += 1,
            }
        }

        Ok(EngineOutput {
            layers: descriptors,
            entries: tree,
            failures,
            uncertainties,
            stats,
        })
    }

    /// Apply one scanned layer to the merged-so-far tree, in the fixed
    /// order: opaque markers, whiteouts, then regular entries.
    fn apply_layer(
        &self,
        index: usize,
        layer_id: &str,
        scanned: Vec<Scanned>,
        tree: &mut BTreeMap<String, FinalEntry>,
        stats: &mut MergeStats,
    ) -> (LayerDescriptor, Vec<Diagnostic>) {
        let mut opaque: Vec<&Scanned> = Vec::new();
        let mut whiteouts: Vec<(&Scanned, String)> = Vec::new();
        let mut regular: Vec<&Scanned> = Vec::new();
        let mut uncertainties = Vec::new();

        for node in &scanned {
            let name = node
                .rel
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or_default();
            match paths::classify_name(name) {
                MarkerKind::OpaqueDir => opaque.push(node),
                MarkerKind::Whiteout(target) => whiteouts.push((node, target)),
                MarkerKind::Regular => regular.push(node),
            }
        }
        // Deterministic order inside each group; parents before children
        // for regular entries so replacement invariants hold.
        let key = |n: &&Scanned| {
            (
                n.rel.components().count(),
                paths::to_report_path(&n.rel).unwrap_or_default(),
            )
        };
        opaque.sort_by_key(key);
        whiteouts.sort_by_key(|(n, _)| key(n));
        regular.sort_by_key(key);

        let mut descriptor = LayerDescriptor {
            index,
            id: layer_id.to_string(),
            root: String::new(),
            entries_scanned: scanned.len(),
            whiteouts_applied: 0,
            opaque_dirs_applied: 0,
        };

        for marker in opaque {
            let dir = marker.rel.parent().map(Path::to_path_buf).unwrap_or_default();
            let dir_key = paths::to_report_path(&dir).unwrap_or_default();
            let removed = remove_children(tree, &dir_key);
            stats.entries_removed_by_opaque += removed;
            descriptor.opaque_dirs_applied += 1;
        }

        for (marker, target_name) in whiteouts {
            let dir = marker.rel.parent().map(Path::to_path_buf).unwrap_or_default();
            let target_rel = dir.join(&target_name);
            let Ok(target_key) = paths::to_report_path(&target_rel) else {
                continue;
            };
            let removed = remove_subtree(tree, &target_key);
            if removed > 0 {
                stats.entries_removed_by_whiteout += removed;
                descriptor.whiteouts_applied += 1;
            }
        }

        for node in regular {
            let Ok(key) = paths::to_report_path(&node.rel) else {
                continue;
            };
            let entry = build_entry(node, index, layer_id, key);
            insert_replacing(tree, entry, stats, &mut uncertainties, index);
        }

        (descriptor, uncertainties)
    }

    /// Post-merge symlink validation pass. Verdicts are computed against
    /// the unmodified tree first, then applied together, so the outcome
    /// never depends on the order links happen to be visited in.
    /// Invalid links are removed and recorded as failures; dangling links
    /// are kept and recorded as uncertainties.
    fn validate_links(
        &self,
        tree: &mut BTreeMap<String, FinalEntry>,
        failures: &mut Vec<Diagnostic>,
        uncertainties: &mut Vec<Diagnostic>,
    ) {
        let link_keys: Vec<String> = tree
            .values()
            .filter(|e| e.kind == EntryKind::Symlink)
            .map(|e| e.path.clone())
            .collect();

        // Phase 1: verdicts against the immutable tree.
        let verdicts: Vec<(String, usize, LinkVerdict)> = link_keys
            .iter()
            .map(|key| self.link_verdict(tree, key))
            .collect();

        // Phase 2: apply.
        for (key, layer, verdict) in verdicts {
            match verdict {
                LinkVerdict::Invalid(category, message) => {
                    failures.push(Diagnostic::failure(
                        category,
                        Some(layer),
                        Some(key.clone()),
                        message,
                    ));
                    tree.remove(&key);
                }
                LinkVerdict::Status(LinkStatus::Internal) => {
                    if let Some(e) = tree.get_mut(&key) {
                        e.link_status = Some(LinkStatus::Internal);
                    }
                }
                LinkVerdict::Status(LinkStatus::Dangling) => {
                    let target = tree
                        .get(&key)
                        .and_then(|e| e.link_target.clone())
                        .unwrap_or_default();
                    uncertainties.push(Diagnostic::uncertain(
                        DiagnosticCategory::DanglingLink,
                        Some(layer),
                        Some(key.clone()),
                        format!("symlink target '{target}' names no entry in the final tree"),
                    ));
                    if let Some(e) = tree.get_mut(&key) {
                        e.link_status = Some(LinkStatus::Dangling);
                    }
                }
            }
        }
    }

    /// Verdict for one symlink, computed against the unmodified tree.
    fn link_verdict(
        &self,
        tree: &BTreeMap<String, FinalEntry>,
        key: &str,
    ) -> (String, usize, LinkVerdict) {
        let entry = tree.get(key).expect("link key collected from tree");
        let target = entry.link_target.clone().unwrap_or_default();
        let layer = entry.source_layer;
        let parent = Path::new(key)
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_default();

        let verdict = if Path::new(&target).is_absolute() {
            LinkVerdict::Invalid(
                DiagnosticCategory::AbsoluteTarget,
                format!("absolute symlink target '{target}' is outside the supported scope"),
            )
        } else {
            match self.resolve_chain(tree, &parent, &target) {
                ChainResult::Resolved(resolved) => {
                    if tree.contains_key(&resolved) {
                        LinkVerdict::Status(LinkStatus::Internal)
                    } else {
                        LinkVerdict::Status(LinkStatus::Dangling)
                    }
                }
                ChainResult::Escapes => LinkVerdict::Invalid(
                    DiagnosticCategory::EscapesRoot,
                    format!("symlink target '{target}' escapes the isolation root"),
                ),
                ChainResult::Loop => LinkVerdict::Invalid(
                    DiagnosticCategory::LinkLoop,
                    format!(
                        "symlink chain from '{target}' exceeds depth {}",
                        self.opts.max_symlink_depth
                    ),
                ),
            }
        };
        (key.to_string(), layer, verdict)
    }

    /// Resolve a symlink target to a normalized root-relative path key,
    /// following intermediate symlinks up to the depth limit.
    fn resolve_chain(
        &self,
        tree: &BTreeMap<String, FinalEntry>,
        parent: &Path,
        target: &str,
    ) -> ChainResult {
        let mut current_base = parent.to_path_buf();
        let mut current_target = target.to_string();
        for _ in 0..self.opts.max_symlink_depth {
            let resolved = match paths::normalize_relative(&current_base, &current_target) {
                Ok(p) => p,
                Err(NormalizeError::EscapesRoot) => return ChainResult::Escapes,
                Err(NormalizeError::NonUtf8) => return ChainResult::Escapes,
            };
            let key = paths::to_report_path(&resolved).unwrap_or_default();
            match tree.get(&key) {
                Some(e) if e.kind == EntryKind::Symlink => {
                    current_base = resolved
                        .parent()
                        .map(Path::to_path_buf)
                        .unwrap_or_default();
                    current_target = e.link_target.clone().unwrap_or_default();
                }
                _ => return ChainResult::Resolved(key),
            }
        }
        ChainResult::Loop
    }
}

enum ChainResult {
    Resolved(String),
    Escapes,
    Loop,
}

/// Outcome of validating one symlink against the final tree.
enum LinkVerdict {
    Invalid(DiagnosticCategory, String),
    Status(LinkStatus),
}

/// Build the final-tree entry for a scanned regular node.
fn build_entry(node: &Scanned, index: usize, layer_id: &str, key: String) -> FinalEntry {
    let base = FinalEntry {
        path: key,
        kind: EntryKind::Dir,
        source_layer: index,
        layer_id: layer_id.to_string(),
        size: None,
        sha256: None,
        link_target: None,
        link_status: None,
    };
    match &node.kind {
        ScannedKind::File { size, sha256 } => FinalEntry {
            kind: EntryKind::File,
            size: Some(*size),
            sha256: Some(sha256.clone()),
            ..base
        },
        ScannedKind::Dir => base,
        ScannedKind::Symlink { target } => FinalEntry {
            kind: EntryKind::Symlink,
            link_target: Some(target.clone()),
            ..base
        },
    }
}

/// Scan one layer directory without following symlinks.
fn scan_layer(
    index: usize,
    layer: &LayerInput,
    failures: &mut Vec<Diagnostic>,
    total_scanned: &mut usize,
    max_entries: usize,
) -> Result<Vec<Scanned>, MergeError> {
    let mut out = Vec::new();
    let walker = WalkDir::new(&layer.root)
        .follow_links(false)
        .sort_by_file_name()
        .into_iter();
    for item in walker {
        let item = item.map_err(|e| MergeError::Io {
            layer: index,
            source: e.into(),
        })?;
        if item.path() == layer.root {
            continue;
        }
        *total_scanned += 1;
        if *total_scanned > max_entries {
            return Err(MergeError::EntryLimitExceeded { limit: max_entries });
        }
        let rel = item
            .path()
            .strip_prefix(&layer.root)
            .expect("walkdir yields paths under root")
            .to_path_buf();
        let report_path = match paths::to_report_path(&rel) {
            Ok(p) => p,
            Err(_) => {
                failures.push(Diagnostic::failure(
                    DiagnosticCategory::NonUtf8Name,
                    Some(index),
                    None,
                    format!("skipping non-UTF-8 name under layer {index}"),
                ));
                continue;
            }
        };
        let meta = item.metadata().map_err(|e| MergeError::Io {
            layer: index,
            source: io::Error::other(e),
        })?;
        let ft = meta.file_type();
        if ft.is_file() {
            let sha256 = hash_file(item.path()).map_err(|e| MergeError::Io {
                layer: index,
                source: e,
            })?;
            out.push(Scanned {
                rel,
                kind: ScannedKind::File {
                    size: meta.len(),
                    sha256,
                },
            });
        } else if ft.is_dir() {
            out.push(Scanned {
                rel,
                kind: ScannedKind::Dir,
            });
        } else if ft.is_symlink() {
            let target = std::fs::read_link(item.path()).map_err(|e| MergeError::Io {
                layer: index,
                source: e,
            })?;
            let target = target.to_str().unwrap_or("").to_string();
            out.push(Scanned {
                rel,
                kind: ScannedKind::Symlink { target },
            });
        } else {
            failures.push(Diagnostic::failure(
                DiagnosticCategory::UnsupportedType,
                Some(index),
                Some(report_path),
                "node is not a regular file, directory or symlink; skipped".to_string(),
            ));
        }
    }
    Ok(out)
}

fn hash_file(path: &Path) -> io::Result<String> {
    let mut hasher = Sha256::new();
    let mut file = std::fs::File::open(path)?;
    io::copy(&mut file, &mut hasher)?;
    Ok(format!("{:x}", hasher.finalize()))
}

/// Remove an entry and everything beneath it; returns how many were removed.
fn remove_subtree(tree: &mut BTreeMap<String, FinalEntry>, key: &str) -> usize {
    let prefix = format!("{key}/");
    let doomed: Vec<String> = tree
        .range(key.to_string()..)
        .take_while(|(k, _)| k.as_str() == key || k.starts_with(&prefix))
        .map(|(k, _)| k.clone())
        .collect();
    let n = doomed.len();
    for k in doomed {
        tree.remove(&k);
    }
    n
}

/// Remove only the strict children of `dir_key` (opaque semantics).
fn remove_children(tree: &mut BTreeMap<String, FinalEntry>, dir_key: &str) -> usize {
    let prefix = if dir_key.is_empty() {
        String::new()
    } else {
        format!("{dir_key}/")
    };
    let doomed: Vec<String> = tree
        .keys()
        .filter(|k| {
            !k.is_empty() && (prefix.is_empty() || k.starts_with(&prefix)) && k.as_str() != dir_key
        })
        .cloned()
        .collect();
    let n = doomed.len();
    for k in doomed {
        tree.remove(&k);
    }
    n
}

/// Insert an entry, replacing any existing node at its path. Two dirs
/// merge (the existing dir keeps its lowest-layer provenance). Replacing
/// a directory with a non-directory drops the whole subtree; replacing a
/// non-directory with a directory drops just the node.
fn insert_replacing(
    tree: &mut BTreeMap<String, FinalEntry>,
    entry: FinalEntry,
    stats: &mut MergeStats,
    uncertainties: &mut Vec<Diagnostic>,
    layer: usize,
) {
    let key = entry.path.clone();
    if let Some(existing) = tree.get(&key) {
        // Directories merge across layers: a dir seen again keeps its
        // original (lowest-layer) provenance — only its children change.
        if existing.kind == EntryKind::Dir && entry.kind == EntryKind::Dir {
            return;
        }
        stats.entries_replaced += 1;
        if existing.kind == EntryKind::Dir && entry.kind != EntryKind::Dir {
            remove_subtree(tree, &key);
        } else {
            tree.remove(&key);
        }
    }
    // Defensive: a scanned child whose parent is not a directory in the
    // merged state cannot arise from a real directory scan, but never
    // silently produce an inconsistent tree.
    if let Some(parent) = Path::new(&key).parent() {
        let parent_key = paths::to_report_path(parent).unwrap_or_default();
        if !parent_key.is_empty() {
            match tree.get(&parent_key) {
                Some(p) if p.kind == EntryKind::Dir => {}
                Some(_) => {
                    uncertainties.push(Diagnostic::uncertain(
                        DiagnosticCategory::InconsistentLayer,
                        Some(layer),
                        Some(key.clone()),
                        format!("parent '{parent_key}' is not a directory; entry skipped"),
                    ));
                    return;
                }
                None => {
                    uncertainties.push(Diagnostic::uncertain(
                        DiagnosticCategory::InconsistentLayer,
                        Some(layer),
                        Some(key.clone()),
                        format!("parent '{parent_key}' missing; entry skipped"),
                    ));
                    return;
                }
            }
        }
    }
    tree.insert(key, entry);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn layer(dir: &Path) -> LayerInput {
        LayerInput {
            id: dir
                .file_name()
                .unwrap()
                .to_string_lossy()
                .into_owned(),
            root: dir.to_path_buf(),
        }
    }

    #[test]
    fn whiteout_then_recreate_same_layer() {
        let tmp = tempfile::tempdir().unwrap();
        let l1 = tmp.path().join("l1");
        let l2 = tmp.path().join("l2");
        fs::create_dir_all(l1.join("app")).unwrap();
        fs::write(l1.join("app/config.txt"), "v1").unwrap();
        fs::create_dir_all(l2.join("app")).unwrap();
        fs::write(l2.join("app/.wh.config.txt"), "").unwrap();
        fs::write(l2.join("app/config.txt"), "v2").unwrap();

        let engine = MergeEngine::new(EngineOptions::default());
        let out = engine.merge(&[layer(&l1), layer(&l2)]).unwrap();
        let e = out.entries.get("app/config.txt").unwrap();
        assert_eq!(e.source_layer, 1);
        assert_eq!(e.size, Some(2));
        assert_eq!(out.stats.entries_removed_by_whiteout, 1);
    }

    #[test]
    fn opaque_dir_hides_lower_children_only() {
        let tmp = tempfile::tempdir().unwrap();
        let l1 = tmp.path().join("l1");
        let l2 = tmp.path().join("l2");
        fs::create_dir_all(l1.join("data")).unwrap();
        fs::write(l1.join("data/old.txt"), "old").unwrap();
        fs::write(l1.join("keep.txt"), "keep").unwrap();
        fs::create_dir_all(l2.join("data")).unwrap();
        fs::write(l2.join("data/.wh..wh..opq"), "").unwrap();
        fs::write(l2.join("data/new.txt"), "new").unwrap();

        let engine = MergeEngine::new(EngineOptions::default());
        let out = engine.merge(&[layer(&l1), layer(&l2)]).unwrap();
        assert!(!out.entries.contains_key("data/old.txt"));
        assert!(out.entries.contains_key("data/new.txt"));
        assert!(out.entries.contains_key("keep.txt"));
        // The directory itself survives, provenance from the lower layer.
        assert_eq!(out.entries["data"].source_layer, 0);
    }

    #[test]
    fn file_dir_swap_both_directions() {
        let tmp = tempfile::tempdir().unwrap();
        let l1 = tmp.path().join("l1");
        let l2 = tmp.path().join("l2");
        fs::create_dir_all(l1.join("x")).unwrap();
        fs::write(l1.join("x/inner.txt"), "inner").unwrap();
        fs::write(l1.join("y"), "was-file").unwrap();
        fs::create_dir_all(l2.join("y")).unwrap();
        fs::write(l2.join("x"), "now-file").unwrap();
        fs::write(l2.join("y/z.txt"), "z").unwrap();

        let engine = MergeEngine::new(EngineOptions::default());
        let out = engine.merge(&[layer(&l1), layer(&l2)]).unwrap();
        assert_eq!(out.entries["x"].kind, EntryKind::File);
        assert!(!out.entries.contains_key("x/inner.txt"));
        assert_eq!(out.entries["y"].kind, EntryKind::Dir);
        assert!(out.entries.contains_key("y/z.txt"));
    }

    #[test]
    fn escaping_and_absolute_links_are_failures() {
        let tmp = tempfile::tempdir().unwrap();
        let l1 = tmp.path().join("l1");
        fs::create_dir_all(&l1).unwrap();
        std::os::unix::fs::symlink("../../outside", l1.join("escape")).unwrap();
        std::os::unix::fs::symlink("/etc/passwd", l1.join("abs")).unwrap();

        let engine = MergeEngine::new(EngineOptions::default());
        let out = engine.merge(&[layer(&l1)]).unwrap();
        assert!(!out.entries.contains_key("escape"));
        assert!(!out.entries.contains_key("abs"));
        let cats: Vec<_> = out.failures.iter().map(|d| d.category).collect();
        assert!(cats.contains(&DiagnosticCategory::EscapesRoot));
        assert!(cats.contains(&DiagnosticCategory::AbsoluteTarget));
    }

    #[test]
    fn link_loop_is_a_failure() {
        let tmp = tempfile::tempdir().unwrap();
        let l1 = tmp.path().join("l1");
        fs::create_dir_all(&l1).unwrap();
        std::os::unix::fs::symlink("loop-b", l1.join("loop-a")).unwrap();
        std::os::unix::fs::symlink("loop-a", l1.join("loop-b")).unwrap();

        let engine = MergeEngine::new(EngineOptions::default());
        let out = engine.merge(&[layer(&l1)]).unwrap();
        assert!(out.entries.is_empty());
        assert!(
            out.failures
                .iter()
                .all(|d| d.category == DiagnosticCategory::LinkLoop)
        );
        assert_eq!(out.failures.len(), 2);
    }

    #[test]
    fn dangling_link_is_uncertain_not_failure() {
        let tmp = tempfile::tempdir().unwrap();
        let l1 = tmp.path().join("l1");
        fs::create_dir_all(&l1).unwrap();
        std::os::unix::fs::symlink("no/such/file", l1.join("dangling")).unwrap();

        let engine = MergeEngine::new(EngineOptions::default());
        let out = engine.merge(&[layer(&l1)]).unwrap();
        assert_eq!(
            out.entries["dangling"].link_status,
            Some(LinkStatus::Dangling)
        );
        assert!(out.failures.is_empty());
        assert_eq!(out.uncertainties.len(), 1);
        assert_eq!(
            out.uncertainties[0].category,
            DiagnosticCategory::DanglingLink
        );
    }

    #[test]
    fn missing_layer_aborts_with_category() {
        let engine = MergeEngine::new(EngineOptions::default());
        let missing = LayerInput {
            id: "gone".into(),
            root: PathBuf::from("/definitely/not/here"),
        };
        let err = engine.merge(&[missing]).unwrap_err();
        assert!(matches!(err, MergeError::LayerUnavailable { layer: 0, .. }));
    }
}
