//! Path normalization and whiteout-marker parsing.
//!
//! Supported scope (documented contract):
//! - Layer-relative paths are normalized component-wise: `.` dropped,
//!   `..` resolved by popping, duplicate separators collapsed.
//! - A `..` that would pop above the isolation root is rejected
//!   ([`NormalizeError::EscapesRoot`]).
//! - Absolute targets are never produced here; callers decide policy.
//! - Non-UTF-8 names are rejected ([`NormalizeError::NonUtf8`]).

use std::fmt;
use std::path::{Component, Path, PathBuf};

/// OCI whiteout prefix and the opaque-directory marker name.
pub const WHITEOUT_PREFIX: &str = ".wh.";
pub const OPAQUE_MARKER: &str = ".wh..wh..opq";

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NormalizeError {
    /// Normalization would climb above the isolation root.
    EscapesRoot,
    /// A path component is not valid UTF-8.
    NonUtf8,
}

impl fmt::Display for NormalizeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            NormalizeError::EscapesRoot => write!(f, "path escapes the isolation root"),
            NormalizeError::NonUtf8 => write!(f, "path component is not valid UTF-8"),
        }
    }
}

impl std::error::Error for NormalizeError {}

/// Normalize a relative path against `base` (itself root-relative),
/// returning a clean root-relative path. `..` at the root is an error.
///
/// `target` must be relative; absolute targets are the caller's policy
/// decision (the engine rejects them as `AbsoluteTarget`).
pub fn normalize_relative(base: &Path, target: &str) -> Result<PathBuf, NormalizeError> {
    let mut stack: Vec<String> = Vec::new();
    for comp in base.components() {
        push_component(&mut stack, comp)?;
    }
    for comp in Path::new(target).components() {
        push_component(&mut stack, comp)?;
    }
    Ok(stack.iter().collect())
}

fn push_component(stack: &mut Vec<String>, comp: Component<'_>) -> Result<(), NormalizeError> {
    match comp {
        Component::Normal(os) => {
            let s = os.to_str().ok_or(NormalizeError::NonUtf8)?;
            stack.push(s.to_string());
        }
        Component::CurDir => {}
        Component::ParentDir => {
            if stack.pop().is_none() {
                return Err(NormalizeError::EscapesRoot);
            }
        }
        // RootDir/Prefix cannot appear in a verified-relative input; treat
        // defensively as an escape attempt.
        Component::RootDir | Component::Prefix(_) => return Err(NormalizeError::EscapesRoot),
    }
    Ok(())
}

/// What a scanned layer entry's file name means for merge semantics.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MarkerKind {
    /// `.wh..wh..opq` — hide all lower-layer children of the parent dir.
    OpaqueDir,
    /// `.wh.<name>` — delete sibling `<name>` from lower layers.
    Whiteout(String),
    /// Any other name — a regular payload entry.
    Regular,
}

/// Classify a single file name (not a full path) as a whiteout marker,
/// an opaque marker, or a regular entry.
pub fn classify_name(name: &str) -> MarkerKind {
    if name == OPAQUE_MARKER {
        return MarkerKind::OpaqueDir;
    }
    if let Some(rest) = name.strip_prefix(WHITEOUT_PREFIX) {
        if !rest.is_empty() {
            return MarkerKind::Whiteout(rest.to_string());
        }
    }
    MarkerKind::Regular
}

/// Convert a root-relative path to the canonical report form:
/// `/`-separated, no leading or trailing slash.
pub fn to_report_path(rel: &Path) -> Result<String, NormalizeError> {
    let mut out = String::new();
    for comp in rel.components() {
        match comp {
            Component::Normal(os) => {
                let s = os.to_str().ok_or(NormalizeError::NonUtf8)?;
                if !out.is_empty() {
                    out.push('/');
                }
                out.push_str(s);
            }
            Component::CurDir => {}
            Component::ParentDir | Component::RootDir | Component::Prefix(_) => {
                return Err(NormalizeError::EscapesRoot)
            }
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_plain_and_dot_components() {
        let p = normalize_relative(Path::new("a/b"), "./c//d.txt").unwrap();
        assert_eq!(p, PathBuf::from("a/b/c/d.txt"));
    }

    #[test]
    fn normalize_parent_within_root() {
        let p = normalize_relative(Path::new("a/b"), "../c.txt").unwrap();
        assert_eq!(p, PathBuf::from("a/c.txt"));
    }

    #[test]
    fn normalize_parent_escaping_root_is_rejected() {
        assert_eq!(
            normalize_relative(Path::new("a"), "../../etc/passwd"),
            Err(NormalizeError::EscapesRoot)
        );
        assert_eq!(
            normalize_relative(Path::new(""), ".."),
            Err(NormalizeError::EscapesRoot)
        );
    }

    #[test]
    fn normalize_absolute_target_is_rejected_here() {
        assert_eq!(
            normalize_relative(Path::new("a"), "/etc/passwd"),
            Err(NormalizeError::EscapesRoot)
        );
    }

    #[test]
    fn classify_whiteout_and_opaque() {
        assert_eq!(classify_name(".wh..wh..opq"), MarkerKind::OpaqueDir);
        assert_eq!(
            classify_name(".wh.config.txt"),
            MarkerKind::Whiteout("config.txt".to_string())
        );
        assert_eq!(classify_name("config.txt"), MarkerKind::Regular);
        // A bare ".wh." prefix with nothing after it is not a valid whiteout.
        assert_eq!(classify_name(".wh."), MarkerKind::Regular);
    }

    #[test]
    fn report_path_is_slash_separated() {
        assert_eq!(
            to_report_path(Path::new("a/b/c.txt")).unwrap(),
            "a/b/c.txt"
        );
        assert_eq!(to_report_path(Path::new("")).unwrap(), "");
    }
}
