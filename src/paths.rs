//! Path normalization and containment.
//!
//! Scope of supported paths (documented contract):
//! * All in-tree paths are relative, `/`-separated, UTF-8, without NUL.
//! * `.` components are collapsed; `..` is resolved lexically and may never
//!   pop above the isolation root.
//! * Symlinks are supported only with relative targets that resolve
//!   (lexically, against the link's parent directory) to a location inside
//!   the merge root. Absolute targets and escaping targets are reported as
//!   uncertainties, never followed.
//! * Hardlinks are outside the supported link scope and are rejected.

use crate::error::{FailureCategory, MergeError};
use std::path::{Component, Path, PathBuf};

/// Normalize a root-relative path into components.
///
/// Rejects absolute paths, NUL bytes, empty results and `..` escaping the
/// root. `.` and duplicate separators are collapsed.
pub fn normalize_rel(input: &str) -> Result<Vec<String>, MergeError> {
    if input.is_empty() {
        return Err(MergeError::new(FailureCategory::InvalidPath, "empty path"));
    }
    if input.contains('\0') {
        return Err(MergeError::new(
            FailureCategory::InvalidPath,
            "path contains NUL byte",
        ));
    }
    if input.starts_with('/') {
        return Err(MergeError::new(
            FailureCategory::PathEscapesRoot,
            format!("absolute path not allowed here: {input:?}"),
        ));
    }
    let mut out: Vec<String> = Vec::new();
    for comp in input.split('/') {
        match comp {
            "" | "." => {}
            ".." => {
                if out.pop().is_none() {
                    return Err(MergeError::new(
                        FailureCategory::PathEscapesRoot,
                        format!("path escapes root: {input:?}"),
                    ));
                }
            }
            c => out.push(c.to_string()),
        }
    }
    if out.is_empty() {
        return Err(MergeError::new(
            FailureCategory::InvalidPath,
            format!("path resolves to the root itself: {input:?}"),
        ));
    }
    Ok(out)
}

/// Resolve a symlink target lexically against the link's parent components.
///
/// Returns `Ok(Some(components))` when the target stays inside the root,
/// `Ok(None)` when it escapes, and `Err` for absolute targets (outside the
/// supported link scope).
pub fn resolve_link_target(
    parent_components: &[String],
    target: &str,
) -> Result<Option<Vec<String>>, MergeError> {
    if target.contains('\0') {
        return Err(MergeError::new(
            FailureCategory::InvalidPath,
            "link target contains NUL byte",
        ));
    }
    if target.starts_with('/') {
        return Err(MergeError::new(
            FailureCategory::SymlinkEscape,
            format!("absolute link target unsupported: {target:?}"),
        ));
    }
    let mut out: Vec<String> = parent_components.to_vec();
    for comp in target.split('/') {
        match comp {
            "" | "." => {}
            ".." => {
                if out.pop().is_none() {
                    return Ok(None);
                }
            }
            c => out.push(c.to_string()),
        }
    }
    Ok(Some(out))
}

/// Lexically normalize an absolute path (no filesystem access).
fn normalize_absolute(p: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for comp in p.components() {
        match comp {
            Component::RootDir => out.push("/"),
            Component::CurDir => {}
            Component::ParentDir => {
                out.pop();
            }
            Component::Normal(c) => out.push(c),
            Component::Prefix(_) => {}
        }
    }
    out
}

/// Resolve `candidate` to an absolute, lexically normalized path and verify
/// it stays under `root` (which must already be canonical).
///
/// Existing paths are canonicalized (resolving symlinks); for not-yet
/// existing paths the nearest existing ancestor is canonicalized and the
/// remainder appended lexically, so a symlinked ancestor cannot smuggle the
/// path out of the root.
pub fn ensure_within_root(root: &Path, candidate: &Path) -> Result<PathBuf, MergeError> {
    let abs = if candidate.is_absolute() {
        candidate.to_path_buf()
    } else {
        std::env::current_dir()?.join(candidate)
    };
    let abs = normalize_absolute(&abs);

    // Find the longest existing ancestor and canonicalize from there.
    let mut existing = abs.clone();
    let mut remainder: Vec<std::ffi::OsString> = Vec::new();
    while !existing.exists() {
        match existing.file_name() {
            Some(name) => {
                remainder.push(name.to_os_string());
                existing.pop();
            }
            None => break,
        }
    }
    let mut resolved = existing
        .canonicalize()
        .map_err(|e| MergeError::new(FailureCategory::Io, format!("canonicalize: {e}")))?;
    for part in remainder.iter().rev() {
        resolved.push(part);
    }
    let resolved = normalize_absolute(&resolved);

    if !resolved.starts_with(root) {
        return Err(MergeError::new(
            FailureCategory::OutsideWorkspace,
            format!(
                "path {} is outside workspace root {}",
                resolved.display(),
                root.display()
            ),
        ));
    }
    Ok(resolved)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_plain_and_dotted_paths() {
        assert_eq!(normalize_rel("a/b/c").unwrap(), vec!["a", "b", "c"]);
        assert_eq!(normalize_rel("./a//b/./c").unwrap(), vec!["a", "b", "c"]);
        assert_eq!(normalize_rel("a/b/../c").unwrap(), vec!["a", "c"]);
    }

    #[test]
    fn rejects_escape_and_invalid() {
        assert_eq!(
            normalize_rel("../x").unwrap_err().category,
            FailureCategory::PathEscapesRoot
        );
        assert_eq!(
            normalize_rel("a/../../x").unwrap_err().category,
            FailureCategory::PathEscapesRoot
        );
        assert_eq!(
            normalize_rel("/abs").unwrap_err().category,
            FailureCategory::PathEscapesRoot
        );
        assert_eq!(
            normalize_rel("").unwrap_err().category,
            FailureCategory::InvalidPath
        );
        assert_eq!(
            normalize_rel("a\0b").unwrap_err().category,
            FailureCategory::InvalidPath
        );
    }

    #[test]
    fn resolves_relative_link_targets() {
        let parent = vec!["etc".to_string()];
        assert_eq!(
            resolve_link_target(&parent, "app.conf").unwrap(),
            Some(vec!["etc".to_string(), "app.conf".to_string()])
        );
        assert_eq!(
            resolve_link_target(&parent, "../data/c.txt").unwrap(),
            Some(vec!["data".to_string(), "c.txt".to_string()])
        );
    }

    #[test]
    fn detects_escaping_and_absolute_targets() {
        let parent = vec!["bad".to_string()];
        // bad/.. -> root, .. -> escape
        assert_eq!(
            resolve_link_target(&parent, "../../external/sentinel.txt").unwrap(),
            None
        );
        assert_eq!(
            resolve_link_target(&parent, "/etc/passwd")
                .unwrap_err()
                .category,
            FailureCategory::SymlinkEscape
        );
    }

    #[test]
    fn containment_check() {
        let root = std::env::temp_dir().canonicalize().unwrap();
        let inside = ensure_within_root(&root, &root.join("some/nested/dir")).unwrap();
        assert!(inside.starts_with(&root));
        let err = ensure_within_root(&root, Path::new("/etc/hostname")).unwrap_err();
        assert_eq!(err.category, FailureCategory::OutsideWorkspace);
    }
}
