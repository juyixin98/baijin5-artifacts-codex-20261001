//! Persistence: sampling state survives a save/load round trip and the
//! resumed engine produces results identical to an uninterrupted run.

mod common;

use common::*;
use procdiff::engine::Engine;
use procdiff::snapshot;
use procdiff::store::Store;

#[test]
fn resumed_engine_matches_uninterrupted_run() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let store = Store::new(tmp.path().to_path_buf());

    let root = fixtures_dir().join("pid_reuse");
    let snaps: Vec<_> = snapshot::list_snapshots(&root)
        .unwrap()
        .into_iter()
        .map(|(_, p)| snapshot::load_snapshot(&p).unwrap())
        .collect();

    // Ingest seq 1..=3, persist, then resume in a fresh engine for 4..=5.
    let mut first = Engine::new(test_config());
    for snap in &snaps[..3] {
        first.ingest(snap);
    }
    store.save(&first.state).expect("save state");

    let loaded = store.load().expect("load state").expect("state exists");
    let mut resumed = Engine::from_state(test_config(), loaded);
    for snap in &snaps[3..] {
        resumed.ingest(snap);
    }

    // Reference: one engine ingesting everything without interruption.
    let reference = ingest_case("pid_reuse");
    assert_eq!(resumed.state.deltas, reference.state.deltas);
    assert_eq!(resumed.state.events, reference.state.events);
    assert_eq!(resumed.state.procs, reference.state.procs);
    assert_eq!(resumed.state.diags, reference.state.diags);
}

#[test]
fn corrupt_state_file_is_an_error_not_a_silent_reset() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let store = Store::new(tmp.path().to_path_buf());
    std::fs::create_dir_all(tmp.path()).unwrap();
    std::fs::write(tmp.path().join("state.json"), "{not json").unwrap();
    assert!(store.load().is_err());
}
