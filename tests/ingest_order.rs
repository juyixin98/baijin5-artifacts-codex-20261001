//! Out-of-order / duplicate snapshots are rejected with a diagnostic record.

mod common;

use common::*;
use procdiff::diag::Decision;
use procdiff::engine::Engine;
use procdiff::snapshot;

#[test]
fn duplicate_snapshot_is_rejected_with_reason() {
    let root = fixtures_dir().join("pid_reuse");
    let snaps: Vec<_> = snapshot::list_snapshots(&root)
        .unwrap()
        .into_iter()
        .map(|(_, p)| snapshot::load_snapshot(&p).unwrap())
        .collect();
    let mut engine = Engine::new(test_config());
    assert!(!engine.ingest(&snaps[0]).rejected);
    assert!(!engine.ingest(&snaps[1]).rejected);

    // Re-ingesting seq 2 after seq 2 must be rejected, not silently applied.
    let report = engine.ingest(&snaps[1]);
    assert!(report.rejected);
    assert!(report
        .reject_reason
        .as_deref()
        .unwrap()
        .contains("not after last ingested seq 2"));

    let diag = engine
        .state
        .diags
        .iter()
        .find(|r| r.decision == Decision::Rejected)
        .expect("rejection diagnostic");
    assert_eq!(diag.action, "ingest");
    assert!(diag.request_id.starts_with("req-"));
    assert_eq!(diag.key_state["last_seq"], 2);
    assert_eq!(diag.key_state["got_seq"], 2);

    // State was not mutated by the rejected snapshot.
    assert_eq!(engine.state.last_seq, Some(2));
}
