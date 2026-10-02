//! Integration tests: run the merge engine against the committed fixtures
//! and compare with hand-written expected trees (fixtures/*/expected.json).
//! The expected files are authored by hand — file hashes in them come from
//! the independent `sha256sum` tool, not from the engine under test.
//!
//! Also asserts the merge is read-only: the fixture layers and the
//! `fixtures/outside` sentinel directory are byte- and mtime-identical
//! before and after every run.

use merge_check::merge::{EngineOptions, MergeEngine};
use merge_check::model::{EntryKind, LayerInput};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

#[derive(Debug, Deserialize)]
struct Expected {
    #[allow(dead_code)]
    scenario: String,
    layers: Vec<String>,
    entries: Vec<ExpectedEntry>,
    failures: Vec<ExpectedDiag>,
    uncertainties: Vec<ExpectedDiag>,
}

#[derive(Debug, Deserialize)]
struct ExpectedEntry {
    path: String,
    kind: String,
    source_layer: usize,
    #[serde(default)]
    size: Option<u64>,
    #[serde(default)]
    sha256: Option<String>,
    #[serde(default)]
    link_target: Option<String>,
    #[serde(default)]
    link_status: Option<String>,
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
struct ExpectedDiag {
    category: String,
    path: String,
}

fn fixtures_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures")
}

fn kind_str(kind: EntryKind) -> &'static str {
    match kind {
        EntryKind::File => "file",
        EntryKind::Dir => "dir",
        EntryKind::Symlink => "symlink",
    }
}

/// Snapshot every regular file under `dir` as (relative path, sha256, mtime).
fn snapshot(dir: &Path) -> BTreeMap<String, (String, std::time::SystemTime)> {
    let mut out = BTreeMap::new();
    for entry in walkdir::WalkDir::new(dir).follow_links(false).sort_by_file_name() {
        let entry = entry.unwrap();
        let meta = entry.metadata().unwrap();
        if !meta.file_type().is_file() {
            continue;
        }
        let rel = entry
            .path()
            .strip_prefix(dir)
            .unwrap()
            .to_string_lossy()
            .into_owned();
        let mut hasher = Sha256::new();
        hasher.update(std::fs::read(entry.path()).unwrap());
        let hash = format!("{:x}", hasher.finalize());
        out.insert(rel, (hash, meta.modified().unwrap()));
    }
    out
}

fn run_scenario(scenario: &str) {
    let dir = fixtures_dir().join(scenario);
    let expected: Expected = serde_json::from_str(
        &std::fs::read_to_string(dir.join("expected.json")).expect("expected.json readable"),
    )
    .expect("expected.json parses");

    let layers: Vec<LayerInput> = expected
        .layers
        .iter()
        .map(|name| LayerInput {
            id: name.clone(),
            root: dir.join(name),
        })
        .collect();

    let engine = MergeEngine::new(EngineOptions::default());
    let out = engine
        .merge(&layers)
        .unwrap_or_else(|e| panic!("scenario {scenario}: merge failed: {e}"));

    // 1. Exact path set equality — no missing, no extra entries.
    let actual_paths: Vec<&String> = out.entries.keys().collect();
    let mut expected_paths: Vec<&String> = expected.entries.iter().map(|e| &e.path).collect();
    expected_paths.sort();
    assert_eq!(
        actual_paths, expected_paths,
        "scenario {scenario}: final tree paths differ"
    );

    // 2. Per-entry field equality, including content hashes and provenance.
    for want in &expected.entries {
        let got = out
            .entries
            .get(&want.path)
            .unwrap_or_else(|| panic!("scenario {scenario}: missing entry {}", want.path));
        assert_eq!(
            kind_str(got.kind),
            want.kind,
            "scenario {scenario}: kind of {}",
            want.path
        );
        assert_eq!(
            got.source_layer, want.source_layer,
            "scenario {scenario}: provenance of {}",
            want.path
        );
        assert_eq!(
            got.size, want.size,
            "scenario {scenario}: size of {}",
            want.path
        );
        assert_eq!(
            got.sha256.as_deref(),
            want.sha256.as_deref(),
            "scenario {scenario}: content hash of {}",
            want.path
        );
        assert_eq!(
            got.link_target.as_deref(),
            want.link_target.as_deref(),
            "scenario {scenario}: link target of {}",
            want.path
        );
        let got_status = got
            .link_status
            .map(|s| serde_json::to_value(s).unwrap().as_str().unwrap().to_string());
        assert_eq!(
            got_status.as_deref(),
            want.link_status.as_deref(),
            "scenario {scenario}: link status of {}",
            want.path
        );
    }

    // 3. Failures and uncertainties match by (category, path), exactly.
    let diag_pairs = |diags: &[merge_check::model::Diagnostic]| -> Vec<ExpectedDiag> {
        let mut v: Vec<ExpectedDiag> = diags
            .iter()
            .map(|d| ExpectedDiag {
                category: serde_json::to_value(d.category)
                    .unwrap()
                    .as_str()
                    .unwrap()
                    .to_string(),
                path: d.path.clone().unwrap_or_default(),
            })
            .collect();
        v.sort();
        v
    };
    let mut want_failures = expected.failures.clone();
    want_failures.sort();
    let mut want_uncertainties = expected.uncertainties.clone();
    want_uncertainties.sort();
    assert_eq!(
        diag_pairs(&out.failures),
        want_failures,
        "scenario {scenario}: failures differ"
    );
    assert_eq!(
        diag_pairs(&out.uncertainties),
        want_uncertainties,
        "scenario {scenario}: uncertainties differ"
    );
}

#[test]
fn scenario_delete_recreate() {
    run_scenario("delete-recreate");
}

#[test]
fn scenario_opaque_dir() {
    run_scenario("opaque-dir");
}

#[test]
fn scenario_file_dir_swap() {
    run_scenario("file-dir-swap");
}

#[test]
fn scenario_malicious() {
    run_scenario("malicious");
}

/// The merge must be purely read-only: every fixture layer and the
/// outside sentinel directory are identical (content + mtime) afterwards.
#[test]
fn merge_does_not_modify_layers_or_outside_dirs() {
    let fixtures = fixtures_dir();
    let before = snapshot(&fixtures);

    let engine = MergeEngine::new(EngineOptions::default());
    for scenario in [
        "delete-recreate",
        "opaque-dir",
        "file-dir-swap",
        "malicious",
    ] {
        let dir = fixtures.join(scenario);
        let layers: Vec<LayerInput> = ["layer1", "layer2"]
            .iter()
            .map(|n| dir.join(n))
            .filter(|p| p.is_dir())
            .map(|root| LayerInput {
                id: root.file_name().unwrap().to_string_lossy().into_owned(),
                root,
            })
            .collect();
        engine.merge(&layers).unwrap();
    }

    let after = snapshot(&fixtures);
    assert_eq!(
        before, after,
        "merge modified files under fixtures/ (including fixtures/outside)"
    );
}

/// Unsupported node types (fifo) are failures with a distinct category,
/// and the run still completes for the rest of the layer.
#[test]
fn unsupported_node_type_is_a_failure() {
    let tmp = tempfile::tempdir().unwrap();
    let layer = tmp.path().join("l1");
    std::fs::create_dir_all(&layer).unwrap();
    std::fs::write(layer.join("ok.txt"), "ok").unwrap();
    let fifo = layer.join("pipe");
    let c_path = std::ffi::CString::new(fifo.to_str().unwrap()).unwrap();
    let rc = unsafe { libc_mkfifo(&c_path) };
    assert_eq!(rc, 0, "mkfifo failed");

    let engine = MergeEngine::new(EngineOptions::default());
    let out = engine
        .merge(&[LayerInput {
            id: "l1".into(),
            root: layer,
        }])
        .unwrap();
    assert!(out.entries.contains_key("ok.txt"));
    assert!(!out.entries.contains_key("pipe"));
    assert_eq!(out.failures.len(), 1);
    assert_eq!(
        out.failures[0].category,
        merge_check::model::DiagnosticCategory::UnsupportedType
    );
    assert_eq!(out.failures[0].path.as_deref(), Some("pipe"));
}

// Tiny libc shim so the test needs no extra dependency.
unsafe fn libc_mkfifo(path: &std::ffi::CStr) -> i32 {
    extern "C" {
        fn mkfifo(path: *const std::os::raw::c_char, mode: u32) -> i32;
    }
    unsafe { mkfifo(path.as_ptr(), 0o644) }
}
