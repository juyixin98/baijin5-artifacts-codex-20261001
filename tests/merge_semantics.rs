//! Merge-semantics tests: run the engine on the good fixture stack and
//! compare the produced tree against the hand-authored reference tree in
//! fixtures/expected/final-tree, plus per-entry provenance assertions.

use merge_checker::merge::{MergeEngine, MergeRequest};
use merge_checker::model::{EntryKind, RunStatus};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

fn manifest() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn layer(n: &str) -> PathBuf {
    manifest().join("fixtures/layers").join(n)
}

/// Snapshot a directory tree: rel-path -> (kind, content-or-target).
/// Built with plain std::fs walking, independent of the merge engine.
fn snapshot_tree(root: &Path) -> BTreeMap<String, (String, Vec<u8>)> {
    let mut out = BTreeMap::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        for entry in std::fs::read_dir(&dir).expect("read_dir") {
            let entry = entry.expect("entry");
            let path = entry.path();
            let rel = path
                .strip_prefix(root)
                .unwrap()
                .to_string_lossy()
                .replace('\\', "/");
            let meta = std::fs::symlink_metadata(&path).expect("metadata");
            if meta.file_type().is_symlink() {
                let target = std::fs::read_link(&path).expect("read_link");
                out.insert(
                    rel,
                    (
                        "symlink".to_string(),
                        target.to_string_lossy().as_bytes().to_vec(),
                    ),
                );
            } else if meta.is_dir() {
                out.insert(rel, ("dir".to_string(), Vec::new()));
                stack.push(path);
            } else {
                out.insert(rel, ("file".to_string(), std::fs::read(&path).expect("read")));
            }
        }
    }
    out
}

fn run_good_stack() -> (merge_checker::model::MergeRun, PathBuf) {
    let out = std::env::temp_dir().join(format!("merge-good-{}", uuid::Uuid::new_v4()));
    let run = MergeEngine::new().execute(&MergeRequest {
        run_id: "run-test-good".to_string(),
        request_id: "req-test-good".to_string(),
        layers: vec![layer("layer1"), layer("layer2"), layer("layer3")],
        output_dir: out.clone(),
    });
    (run, out)
}

#[test]
fn final_tree_matches_hand_authored_reference() {
    let (run, out) = run_good_stack();
    assert_eq!(run.status, RunStatus::Success, "failures: {:?}", run.failures);
    assert!(run.failures.is_empty());
    assert!(run.uncertainties.is_empty());

    let produced = snapshot_tree(&out);
    let expected = snapshot_tree(&manifest().join("fixtures/expected/final-tree"));
    assert_eq!(
        produced, expected,
        "produced final tree differs from hand-authored reference"
    );
    let _ = std::fs::remove_dir_all(&out);
}

#[test]
fn delete_then_recreate_yields_upper_layer_content() {
    let (run, out) = run_good_stack();
    // layer1 wrote app.conf, layer2 whiteouted it, layer3 recreated it.
    let content = std::fs::read_to_string(out.join("etc/app.conf")).unwrap();
    assert_eq!(content, "app-version=3\n");
    let entry = run.entry("etc/app.conf").expect("entry recorded");
    assert_eq!(entry.kind, EntryKind::File);
    assert_eq!(entry.source.index, 2, "must come from layer3 (index 2)");
    assert!(entry.sha256.is_some());
    let _ = std::fs::remove_dir_all(&out);
}

#[test]
fn opaque_dir_hides_lower_layer_contents() {
    let (run, out) = run_good_stack();
    // layer2 marked data/ opaque: layer1's a.txt and b.txt must be gone,
    // only layer2's c.txt remains.
    assert!(!out.join("data/a.txt").exists());
    assert!(!out.join("data/b.txt").exists());
    assert_eq!(
        std::fs::read_to_string(out.join("data/c.txt")).unwrap(),
        "C from layer2\n"
    );
    assert!(run.entry("data/a.txt").is_none());
    assert!(run.entry("data/b.txt").is_none());
    assert_eq!(run.entry("data/c.txt").unwrap().source.index, 1);
    let _ = std::fs::remove_dir_all(&out);
}

#[test]
fn same_name_file_dir_swap_produces_directory() {
    let (run, out) = run_good_stack();
    // layer1 had file "swap"; layer2 whiteouted it and created dir "swap".
    assert!(out.join("swap").is_dir());
    assert_eq!(
        std::fs::read_to_string(out.join("swap/inner.txt")).unwrap(),
        "swap is now a dir (layer2)\n"
    );
    let dir_entry = run.entry("swap").expect("swap dir recorded");
    assert_eq!(dir_entry.kind, EntryKind::Dir);
    assert_eq!(dir_entry.source.index, 1, "dir must come from layer2");
    assert_eq!(run.entry("swap/inner.txt").unwrap().source.index, 1);
    let _ = std::fs::remove_dir_all(&out);
}

#[test]
fn in_root_symlink_is_preserved() {
    let (run, out) = run_good_stack();
    let target = std::fs::read_link(out.join("etc/current")).unwrap();
    assert_eq!(target.to_string_lossy(), "app.conf");
    let entry = run.entry("etc/current").expect("symlink recorded");
    assert_eq!(entry.kind, EntryKind::Symlink);
    assert_eq!(entry.link_target.as_deref(), Some("app.conf"));
    assert_eq!(entry.source.index, 2);
    let _ = std::fs::remove_dir_all(&out);
}

#[test]
fn provenance_covers_untouched_lower_layer_entries() {
    let (run, out) = run_good_stack();
    // keep/keep.txt was never touched by upper layers: provenance layer 0.
    assert_eq!(run.entry("keep/keep.txt").unwrap().source.index, 0);
    assert_eq!(
        std::fs::read_to_string(out.join("keep/keep.txt")).unwrap(),
        "keep me\n"
    );
    // Every recorded entry names a real source layer.
    for e in &run.entries {
        assert!(e.source.index < 3, "entry {} has bogus layer", e.path);
    }
    let _ = std::fs::remove_dir_all(&out);
}

#[test]
fn whiteout_markers_never_appear_in_output() {
    let (run, out) = run_good_stack();
    let tree = snapshot_tree(&out);
    for path in tree.keys() {
        let base = path.rsplit('/').next().unwrap();
        assert!(
            !base.starts_with(".wh."),
            "whiteout marker leaked into output: {path}"
        );
    }
    assert!(run.entries.iter().all(|e| !e.path.contains(".wh.")));
    let _ = std::fs::remove_dir_all(&out);
}

#[test]
fn missing_layer_is_a_fatal_failure_with_category() {
    let out = std::env::temp_dir().join(format!("merge-missing-{}", uuid::Uuid::new_v4()));
    let run = MergeEngine::new().execute(&MergeRequest {
        run_id: "run-test-missing".to_string(),
        request_id: "req-test-missing".to_string(),
        layers: vec![manifest().join("fixtures/layers/does-not-exist")],
        output_dir: out.clone(),
    });
    assert_eq!(run.status, RunStatus::Failed);
    assert_eq!(run.failures.len(), 1);
    assert_eq!(
        run.failures[0].category,
        merge_checker::error::FailureCategory::LayerNotFound
    );
    assert!(!out.exists(), "no output may be produced on fatal failure");
}

#[test]
fn nonempty_output_dir_is_rejected() {
    let out = std::env::temp_dir().join(format!("merge-nonempty-{}", uuid::Uuid::new_v4()));
    std::fs::create_dir_all(&out).unwrap();
    std::fs::write(out.join("pre-existing.txt"), b"x").unwrap();
    let run = MergeEngine::new().execute(&MergeRequest {
        run_id: "run-test-nonempty".to_string(),
        request_id: "req-test-nonempty".to_string(),
        layers: vec![layer("layer1")],
        output_dir: out.clone(),
    });
    assert_eq!(run.status, RunStatus::Failed);
    assert_eq!(
        run.failures[0].category,
        merge_checker::error::FailureCategory::OutputDirNotEmpty
    );
    // The pre-existing content must be untouched.
    assert_eq!(
        std::fs::read_to_string(out.join("pre-existing.txt")).unwrap(),
        "x"
    );
    let _ = std::fs::remove_dir_all(&out);
}
