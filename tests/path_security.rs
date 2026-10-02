//! Path-security tests: malicious fixtures must be classified into the
//! right failure/uncertainty categories, and nothing outside the output
//! directory may be modified.

use merge_checker::error::FailureCategory;
use merge_checker::merge::{MergeEngine, MergeRequest};
use merge_checker::model::{RunStatus, UncertaintyKind};
use std::path::PathBuf;

fn manifest() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn malicious(n: &str) -> PathBuf {
    manifest().join("fixtures/malicious").join(n)
}

fn fresh_out(tag: &str) -> PathBuf {
    std::env::temp_dir().join(format!("merge-sec-{tag}-{}", uuid::Uuid::new_v4()))
}

/// Snapshot of a directory: sorted relative file paths + contents.
fn dir_fingerprint(root: &PathBuf) -> Vec<(String, Vec<u8>)> {
    let mut out = Vec::new();
    let mut stack = vec![root.clone()];
    while let Some(dir) = stack.pop() {
        for entry in std::fs::read_dir(&dir).expect("read_dir") {
            let path = entry.expect("entry").path();
            let rel = path
                .strip_prefix(root)
                .unwrap()
                .to_string_lossy()
                .to_string();
            if path.is_dir() {
                stack.push(path);
            } else {
                out.push((rel, std::fs::read(&path).expect("read")));
            }
        }
    }
    out.sort();
    out
}

#[test]
fn escaping_symlink_is_an_uncertainty_and_external_dir_is_untouched() {
    let external = manifest().join("fixtures/external");
    let before = dir_fingerprint(&external);

    let out = fresh_out("escape");
    let run = MergeEngine::new().execute(&MergeRequest {
        run_id: "run-test-escape".to_string(),
        request_id: "req-test-escape".to_string(),
        layers: vec![malicious("layer-escape")],
        output_dir: out.clone(),
    });

    // Classified as an uncertainty, not silently followed, not fatal.
    assert_eq!(run.status, RunStatus::WithUncertainties);
    assert_eq!(run.uncertainties.len(), 1);
    assert_eq!(
        run.uncertainties[0].reason,
        UncertaintyKind::SymlinkTargetOutsideRoot
    );
    assert_eq!(run.uncertainties[0].path, "bad/evil");
    // The link must not exist in the output.
    assert!(!out.join("bad/evil").exists());
    assert!(run.entry("bad/evil").is_none());
    // The external directory the link pointed at is byte-for-byte untouched.
    let after = dir_fingerprint(&external);
    assert_eq!(before, after, "external directory was modified");
    let _ = std::fs::remove_dir_all(&out);
}

#[test]
fn absolute_symlink_is_an_uncertainty() {
    let out = fresh_out("abslink");
    let run = MergeEngine::new().execute(&MergeRequest {
        run_id: "run-test-abslink".to_string(),
        request_id: "req-test-abslink".to_string(),
        layers: vec![malicious("layer-abslink")],
        output_dir: out.clone(),
    });
    assert_eq!(run.status, RunStatus::WithUncertainties);
    assert_eq!(run.uncertainties.len(), 1);
    assert_eq!(
        run.uncertainties[0].reason,
        UncertaintyKind::AbsoluteSymlinkUnsupported
    );
    assert!(!out.join("abs").exists());
    let _ = std::fs::remove_dir_all(&out);
}

#[test]
fn hardlink_is_a_typed_failure() {
    let out = fresh_out("hardlink");
    let run = MergeEngine::new().execute(&MergeRequest {
        run_id: "run-test-hardlink".to_string(),
        request_id: "req-test-hardlink".to_string(),
        layers: vec![malicious("layer-hardlink")],
        output_dir: out.clone(),
    });
    assert_eq!(run.status, RunStatus::WithFailures);
    assert_eq!(run.failures.len(), 1);
    assert_eq!(
        run.failures[0].category,
        FailureCategory::UnsupportedHardlink
    );
    assert_eq!(run.failures[0].path.as_deref(), Some("x/second.txt"));
    // The first (non-hardlinked) occurrence is still produced.
    assert_eq!(
        std::fs::read_to_string(out.join("x/first.txt")).unwrap(),
        "shared inode\n"
    );
    assert!(!out.join("x/second.txt").exists());
    let _ = std::fs::remove_dir_all(&out);
}

#[test]
fn malformed_whiteout_name_is_a_typed_failure() {
    let out = fresh_out("badwhiteout");
    let run = MergeEngine::new().execute(&MergeRequest {
        run_id: "run-test-badwhiteout".to_string(),
        request_id: "req-test-badwhiteout".to_string(),
        layers: vec![malicious("layer-badwhiteout")],
        output_dir: out.clone(),
    });
    assert_eq!(run.status, RunStatus::WithFailures);
    assert_eq!(run.failures.len(), 1);
    assert_eq!(run.failures[0].category, FailureCategory::InvalidPath);
    // The malformed marker must not leak into the output.
    assert!(!out.join(".wh.").exists());
    let _ = std::fs::remove_dir_all(&out);
}

#[test]
fn combined_malicious_stack_lists_failures_and_uncertainties_separately() {
    let out = fresh_out("combined");
    let run = MergeEngine::new().execute(&MergeRequest {
        run_id: "run-test-combined".to_string(),
        request_id: "req-test-combined".to_string(),
        layers: vec![
            malicious("layer-hardlink"),
            malicious("layer-escape"),
            malicious("layer-abslink"),
        ],
        output_dir: out.clone(),
    });
    assert_eq!(run.status, RunStatus::WithFailuresAndUncertainties);
    assert_eq!(run.failures.len(), 1);
    assert_eq!(run.uncertainties.len(), 2);
    // Failures and uncertainties are separate lists with typed categories.
    assert!(run
        .failures
        .iter()
        .all(|f| f.category == FailureCategory::UnsupportedHardlink));
    assert!(run
        .uncertainties
        .iter()
        .any(|u| u.reason == UncertaintyKind::SymlinkTargetOutsideRoot));
    assert!(run
        .uncertainties
        .iter()
        .any(|u| u.reason == UncertaintyKind::AbsoluteSymlinkUnsupported));
    let _ = std::fs::remove_dir_all(&out);
}
