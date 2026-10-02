//! Merge engine: applies OCI-style layers bottom-to-top into a final tree.
//!
//! Semantics (fixed, documented):
//! * Layers are applied in the given order; upper layers override lower ones.
//! * A whiteout file `.wh.<name>` inside directory D deletes `D/<name>`
//!   (and its subtree) from all lower layers. Upper layers may re-create the
//!   path afterwards ("delete then recreate").
//! * An opaque marker `.wh..wh..opq` inside directory D hides everything
//!   lower layers contributed strictly below D; D itself survives.
//! * Whiteout files never appear in the final tree.
//! * A node whose type changes between layers replaces the old node; if the
//!   old node was a directory its subtree is dropped. A directory that stays
//!   a directory merges and keeps the provenance of the lowest layer that
//!   created it.
//! * Parent directories implied by a file's path are materialized with the
//!   provenance of the layer that first implied them.
//! * The final tree is produced by content: regular files are copied and
//!   hashed; nothing from any image is executed.

use crate::error::FailureCategory;
use crate::model::{
    EntryKind, EntryRecord, FailureRecord, LayerRef, MergeRun, StepRecord, UncertaintyKind,
    UncertaintyRecord, TOOL_VERSION,
};
use crate::paths;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

const WHITEOUT_PREFIX: &str = ".wh.";
const OPAQUE_MARKER: &str = ".wh..wh..opq";

/// In-memory node of the accumulating tree.
#[derive(Debug, Clone)]
enum Node {
    File { layer: LayerRef },
    Dir { layer: LayerRef },
    Symlink { layer: LayerRef, target: String },
}

impl Node {
    fn layer(&self) -> &LayerRef {
        match self {
            Node::File { layer } | Node::Dir { layer } | Node::Symlink { layer, .. } => layer,
        }
    }
}

/// The merge engine. Stateless; one `execute` call per run.
pub struct MergeEngine {
    steps: Vec<StepRecord>,
    failures: Vec<FailureRecord>,
    uncertainties: Vec<UncertaintyRecord>,
    /// (dev, inode) pairs already emitted, for hardlink detection.
    seen_inodes: HashSet<(u64, u64)>,
}

/// Inputs for one merge run.
pub struct MergeRequest {
    pub run_id: String,
    pub request_id: String,
    /// Layer directories in apply order (bottom first).
    pub layers: Vec<PathBuf>,
    /// Must not exist or be empty; created otherwise.
    pub output_dir: PathBuf,
}

impl MergeEngine {
    pub fn new() -> Self {
        Self {
            steps: Vec::new(),
            failures: Vec::new(),
            uncertainties: Vec::new(),
            seen_inodes: HashSet::new(),
        }
    }

    /// Execute one merge run and return the full run record.
    ///
    /// Fatal problems (missing layer, unwritable output) are returned as a
    /// run with status `Failed`; per-entry problems are recorded in
    /// `failures` / `uncertainties` and the run continues.
    pub fn execute(mut self, req: &MergeRequest) -> MergeRun {
        let started_at = now_rfc3339();
        let layers: Vec<LayerRef> = req
            .layers
            .iter()
            .enumerate()
            .map(|(index, p)| LayerRef {
                index,
                path: p.display().to_string(),
            })
            .collect();

        let mut tree: BTreeMap<PathBuf, Node> = BTreeMap::new();
        let mut fatal: Option<FailureRecord> = None;

        for (i, dir) in req.layers.iter().enumerate() {
            let lref = layers[i].clone();
            self.step(
                "scan-layer",
                format!("layer {} at {}", lref.index, lref.path),
            );
            if !dir.is_dir() {
                fatal = Some(FailureRecord {
                    category: FailureCategory::LayerNotFound,
                    path: None,
                    layer: Some(lref.clone()),
                    message: format!("layer directory {} not found", dir.display()),
                });
                break;
            }
            self.scan_layer(dir, Path::new(""), &lref, &mut tree);
            self.step(
                "apply-layer",
                format!("layer {} applied; tree now holds {} nodes", i, tree.len()),
            );
        }

        let mut entries: Vec<EntryRecord> = Vec::new();
        if fatal.is_none() {
            self.step(
                "materialize",
                format!("writing final tree to {}", req.output_dir.display()),
            );
            match materialize(&tree, &req.output_dir, &layers) {
                Ok(materialized) => entries = materialized,
                Err(f) => fatal = Some(f),
            }
        }

        let finished_at = now_rfc3339();
        let status = match fatal {
            Some(ref f) => {
                self.failures.push(f.clone());
                crate::model::RunStatus::Failed
            }
            None => crate::model::RunStatus::from_counts(
                self.failures.len(),
                self.uncertainties.len(),
            ),
        };
        self.step(
            "finish",
            format!(
                "status={:?} entries={} failures={} uncertainties={}",
                status,
                entries.len(),
                self.failures.len(),
                self.uncertainties.len()
            ),
        );

        MergeRun {
            run_id: req.run_id.clone(),
            request_id: req.request_id.clone(),
            tool_version: TOOL_VERSION.to_string(),
            started_at,
            finished_at,
            status,
            layers,
            output_dir: req.output_dir.display().to_string(),
            entries,
            failures: self.failures,
            uncertainties: self.uncertainties,
            steps: self.steps,
        }
    }

    fn step(&mut self, name: &str, detail: String) {
        let seq = self.steps.len() as u32 + 1;
        self.steps.push(StepRecord {
            seq,
            name: name.to_string(),
            detail,
        });
    }

    /// Recursively scan one layer directory, applying every entry to `tree`.
    fn scan_layer(
        &mut self,
        dir: &Path,
        rel: &Path,
        layer: &LayerRef,
        tree: &mut BTreeMap<PathBuf, Node>,
    ) {
        let mut read = match fs::read_dir(dir) {
            Ok(r) => r,
            Err(e) => {
                self.failures.push(FailureRecord {
                    category: FailureCategory::Io,
                    path: Some(rel.display().to_string()),
                    layer: Some(layer.clone()),
                    message: format!("read_dir {}: {e}", dir.display()),
                });
                return;
            }
        };
        let mut names: Vec<_> = read
            .by_ref()
            .filter_map(|e| e.ok())
            .map(|e| e.file_name())
            .collect();
        names.sort();

        for name in names {
            let Some(name_str) = name.to_str().map(str::to_string) else {
                self.failures.push(FailureRecord {
                    category: FailureCategory::InvalidPath,
                    path: Some(rel.join(&name).display().to_string()),
                    layer: Some(layer.clone()),
                    message: "non-UTF-8 file name".to_string(),
                });
                continue;
            };
            let child_rel = rel.join(&name);
            let child_abs = dir.join(&name);

            // Whiteout markers are control entries, never tree nodes.
            if name_str == OPAQUE_MARKER {
                apply_opaque(tree, rel);
                continue;
            }
            if let Some(target) = name_str.strip_prefix(WHITEOUT_PREFIX) {
                if target.is_empty() || target.contains('/') {
                    self.failures.push(FailureRecord {
                        category: FailureCategory::InvalidPath,
                        path: Some(child_rel.display().to_string()),
                        layer: Some(layer.clone()),
                        message: format!("malformed whiteout name {name_str:?}"),
                    });
                    continue;
                }
                apply_tombstone(tree, &rel.join(target));
                continue;
            }

            let meta = match fs::symlink_metadata(&child_abs) {
                Ok(m) => m,
                Err(e) => {
                    self.failures.push(FailureRecord {
                        category: FailureCategory::Io,
                        path: Some(child_rel.display().to_string()),
                        layer: Some(layer.clone()),
                        message: format!("stat: {e}"),
                    });
                    continue;
                }
            };
            let ft = meta.file_type();
            if ft.is_dir() {
                apply_dir(tree, &child_rel, layer);
                self.scan_layer(&child_abs, &child_rel, layer, tree);
            } else if ft.is_file() {
                self.apply_file(&child_rel, &meta, layer, tree);
            } else if ft.is_symlink() {
                self.apply_symlink(&child_abs, &child_rel, rel, layer, tree);
            } else {
                self.failures.push(FailureRecord {
                    category: FailureCategory::UnsupportedFileType,
                    path: Some(child_rel.display().to_string()),
                    layer: Some(layer.clone()),
                    message: "only regular files, directories and symlinks are supported"
                        .to_string(),
                });
            }
        }
    }

    fn apply_file(
        &mut self,
        rel: &Path,
        meta: &fs::Metadata,
        layer: &LayerRef,
        tree: &mut BTreeMap<PathBuf, Node>,
    ) {
        use std::os::unix::fs::MetadataExt;
        if meta.nlink() > 1 {
            let key = (meta.dev(), meta.ino());
            if !self.seen_inodes.insert(key) {
                self.failures.push(FailureRecord {
                    category: FailureCategory::UnsupportedHardlink,
                    path: Some(rel.display().to_string()),
                    layer: Some(layer.clone()),
                    message: "hardlinked file: hardlinks are outside the supported link scope"
                        .to_string(),
                });
                return;
            }
        }
        apply_node(tree, rel, Node::File { layer: layer.clone() });
    }

    fn apply_symlink(
        &mut self,
        abs: &Path,
        rel: &Path,
        parent_rel: &Path,
        layer: &LayerRef,
        tree: &mut BTreeMap<PathBuf, Node>,
    ) {
        let target = match fs::read_link(abs) {
            Ok(t) => t,
            Err(e) => {
                self.failures.push(FailureRecord {
                    category: FailureCategory::Io,
                    path: Some(rel.display().to_string()),
                    layer: Some(layer.clone()),
                    message: format!("read_link: {e}"),
                });
                return;
            }
        };
        let Some(target_str) = target.to_str().map(str::to_string) else {
            self.failures.push(FailureRecord {
                category: FailureCategory::InvalidPath,
                path: Some(rel.display().to_string()),
                layer: Some(layer.clone()),
                message: "non-UTF-8 link target".to_string(),
            });
            return;
        };
        let parent_components: Vec<String> = parent_rel
            .components()
            .filter_map(|c| c.as_os_str().to_str().map(str::to_string))
            .collect();
        match paths::resolve_link_target(&parent_components, &target_str) {
            Ok(Some(_)) => {
                apply_node(
                    tree,
                    rel,
                    Node::Symlink {
                        layer: layer.clone(),
                        target: target_str,
                    },
                );
            }
            Ok(None) => {
                self.uncertainties.push(UncertaintyRecord {
                    reason: UncertaintyKind::SymlinkTargetOutsideRoot,
                    path: rel.display().to_string(),
                    layer: layer.clone(),
                    message: format!(
                        "symlink target {target_str:?} resolves outside the merge root; entry skipped"
                    ),
                });
            }
            Err(e) => {
                self.uncertainties.push(UncertaintyRecord {
                    reason: UncertaintyKind::AbsoluteSymlinkUnsupported,
                    path: rel.display().to_string(),
                    layer: layer.clone(),
                    message: format!("{}; entry skipped", e.message),
                });
            }
        }
    }
}

/// Remove `path` and everything below it from the tree.
fn apply_tombstone(tree: &mut BTreeMap<PathBuf, Node>, path: &Path) {
    tree.retain(|k, _| !(k == path || k.starts_with(path)));
}

/// Hide everything strictly below `dir` (opaque directory marker).
fn apply_opaque(tree: &mut BTreeMap<PathBuf, Node>, dir: &Path) {
    tree.retain(|k, _| k == dir || !k.starts_with(dir));
}

/// Insert or replace a directory node; a pre-existing directory keeps its
/// original (lowest-layer) provenance.
fn apply_dir(tree: &mut BTreeMap<PathBuf, Node>, path: &Path, layer: &LayerRef) {
    if matches!(tree.get(path), Some(Node::Dir { .. })) {
        return;
    }
    apply_node(tree, path, Node::Dir { layer: layer.clone() });
}

/// Insert a node, replacing any existing node of a different kind (dropping
/// its subtree when it was a directory) and ensuring implicit parents.
fn apply_node(tree: &mut BTreeMap<PathBuf, Node>, path: &Path, node: Node) {
    if let Some(existing) = tree.get(path) {
        let replacing_dir = matches!(existing, Node::Dir { .. })
            && !matches!(node, Node::Dir { .. });
        if replacing_dir {
            tree.retain(|k, _| !(k.starts_with(path) && k != path));
        }
    }
    // Materialize implicit parent directories with this layer's provenance.
    let mut ancestor = path.to_path_buf();
    while ancestor.pop() && !ancestor.as_os_str().is_empty() {
        if !tree.contains_key(&ancestor) {
            tree.insert(
                ancestor.clone(),
                Node::Dir {
                    layer: node.layer().clone(),
                },
            );
        }
    }
    tree.insert(path.to_path_buf(), node);
}

/// Write the accumulated tree to `output_dir`, hashing file contents.
fn materialize(
    tree: &BTreeMap<PathBuf, Node>,
    output_dir: &Path,
    _layers: &[LayerRef],
) -> Result<Vec<EntryRecord>, FailureRecord> {
    let fatal = |category, msg: String| FailureRecord {
        category,
        path: None,
        layer: None,
        message: msg,
    };
    if output_dir.exists() {
        let mut is_empty = true;
        if output_dir.is_dir() {
            if let Ok(mut rd) = fs::read_dir(output_dir) {
                is_empty = rd.next().is_none();
            }
        }
        if !output_dir.is_dir() || !is_empty {
            return Err(fatal(
                FailureCategory::OutputDirNotEmpty,
                format!("output directory {} exists and is not empty", output_dir.display()),
            ));
        }
    } else {
        fs::create_dir_all(output_dir).map_err(|e| {
            fatal(
                FailureCategory::Io,
                format!("create output dir {}: {e}", output_dir.display()),
            )
        })?;
    }

    let mut entries = Vec::with_capacity(tree.len());
    for (rel, node) in tree {
        // rel comes from validated fs entries; it can never contain `..`.
        let dest = output_dir.join(rel);
        let rel_str = rel.display().to_string();
        match node {
            Node::Dir { layer } => {
                fs::create_dir_all(&dest).map_err(|e| {
                    fatal(FailureCategory::Io, format!("mkdir {}: {e}", dest.display()))
                })?;
                entries.push(EntryRecord {
                    path: rel_str,
                    kind: EntryKind::Dir,
                    source: layer.clone(),
                    sha256: None,
                    size: None,
                    link_target: None,
                });
            }
            Node::File { layer } => {
                let src = Path::new(&layer.path).join(rel);
                let bytes = fs::read(&src).map_err(|e| {
                    fatal(FailureCategory::Io, format!("read {}: {e}", src.display()))
                })?;
                if let Some(parent) = dest.parent() {
                    fs::create_dir_all(parent).map_err(|e| {
                        fatal(FailureCategory::Io, format!("mkdir {}: {e}", parent.display()))
                    })?;
                }
                fs::write(&dest, &bytes).map_err(|e| {
                    fatal(FailureCategory::Io, format!("write {}: {e}", dest.display()))
                })?;
                let digest = hex_sha256(&bytes);
                entries.push(EntryRecord {
                    path: rel_str,
                    kind: EntryKind::File,
                    source: layer.clone(),
                    sha256: Some(digest),
                    size: Some(bytes.len() as u64),
                    link_target: None,
                });
            }
            Node::Symlink { layer, target } => {
                if let Some(parent) = dest.parent() {
                    fs::create_dir_all(parent).map_err(|e| {
                        fatal(FailureCategory::Io, format!("mkdir {}: {e}", parent.display()))
                    })?;
                }
                std::os::unix::fs::symlink(target, &dest).map_err(|e| {
                    fatal(
                        FailureCategory::Io,
                        format!("symlink {}: {e}", dest.display()),
                    )
                })?;
                entries.push(EntryRecord {
                    path: rel_str,
                    kind: EntryKind::Symlink,
                    source: layer.clone(),
                    sha256: None,
                    size: None,
                    link_target: Some(target.clone()),
                });
            }
        }
    }
    Ok(entries)
}

fn hex_sha256(bytes: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(bytes);
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

fn now_rfc3339() -> String {
    chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true)
}
