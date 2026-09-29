//! Replay-record and truncation tests. Every execution must produce a record
//! keyed by a run id, containing fingerprints, the checkpoint, stats and a
//! rationale; a failure must be replayable from that record.

mod common;

use iejoin_range::operator::{Comparator, JoinPlan, Predicate};
use iejoin_range::replay::{OutcomeKind, ReplayLogger};
use iejoin_range::resource::{Budget, Truncation};
use iejoin_range::validation::prepare;
use iejoin_range::{Checkpoint, ErrorCode};

fn plan() -> JoinPlan {
    JoinPlan::new(
        Predicate {
            left_col: 0,
            right_col: 0,
            op: Comparator::Gt,
        },
        Predicate {
            left_col: 1,
            right_col: 1,
            op: Comparator::Lt,
        },
    )
}

#[test]
fn successful_run_record_has_id_fingerprint_and_rationale() {
    let logger = ReplayLogger::memory(16);
    let ds = common::hand_small();
    let prepared = prepare(&plan(), &ds.left, &ds.right, &Budget::unlimited()).unwrap();
    let page = prepared.run_page(&Budget::unlimited(), Checkpoint::start());

    let run_id = logger.record(iejoin_range::replay::ReplayRecord {
        run_id: iejoin_range::replay::RunId::generate().to_string(),
        outcome: OutcomeKind::Completed,
        plan: plan(),
        budget: iejoin_range::replay::BudgetSnapshot::from(Budget::unlimited()),
        left: iejoin_range::replay::BatchFingerprint::of(&ds.left),
        right: iejoin_range::replay::BatchFingerprint::of(&ds.right),
        checkpoint: page.next.into(),
        stats: page.stats,
        truncation: Some(page.truncation),
        error: None,
        rationale: "test rationale".into(),
    });

    let rec = logger.get(&run_id).expect("record stored");
    assert_eq!(rec.outcome, OutcomeKind::Completed);
    assert_eq!(rec.left.rows, ds.left.row_count());
    assert_eq!(rec.right.rows, ds.right.row_count());
    // Fingerprints detect content: same data → same hash.
    assert_eq!(
        rec.left,
        iejoin_range::replay::BatchFingerprint::of(&ds.left)
    );
    assert!(rec.rationale.contains("test rationale"));
    assert!(rec.run_id.starts_with("run-"));
    assert_eq!(rec.stats.emitted, 5, "hand fixture gt/lt yields five pairs");
}

#[test]
fn truncated_run_record_marks_outcome_and_resumable_checkpoint() {
    let logger = ReplayLogger::memory(16);
    let ds = common::hand_small();
    let page_budget = Budget::new(2, u64::MAX);
    let prepared = prepare(&plan(), &ds.left, &ds.right, &page_budget).unwrap();
    let page1 = prepared.run_page(&page_budget, Checkpoint::start());
    assert_eq!(page1.truncation, Truncation::OutputLimit);
    assert!(!page1.finished);
    assert_eq!(page1.stats.emitted, 2);

    let id1 = logger.record(iejoin_range::replay::ReplayRecord {
        run_id: iejoin_range::replay::RunId::generate().to_string(),
        outcome: OutcomeKind::Truncated,
        plan: plan(),
        budget: iejoin_range::replay::BudgetSnapshot::from(page_budget),
        left: iejoin_range::replay::BatchFingerprint::of(&ds.left),
        right: iejoin_range::replay::BatchFingerprint::of(&ds.right),
        checkpoint: page1.next.into(),
        stats: page1.stats,
        truncation: Some(page1.truncation),
        error: None,
        rationale: "truncated".into(),
    });

    // Replay: resume from the recorded checkpoint and drain the rest.
    let rec = logger.get(&id1).unwrap();
    assert_eq!(rec.outcome, OutcomeKind::Truncated);
    let cp = Checkpoint::from_parts(
        rec.checkpoint.dpos,
        rec.checkpoint.act_pos,
        rec.checkpoint.probe_pos,
        rec.checkpoint.probe_hi,
    );
    let mut total = page1.stats.emitted;
    let mut next = cp;
    loop {
        let p = prepared.run_page(&page_budget, next);
        total += p.stats.emitted;
        next = p.next;
        if p.finished {
            break;
        }
    }
    let full = prepared.run_page(&Budget::unlimited(), Checkpoint::start());
    assert_eq!(
        total, full.stats.emitted,
        "resumed pages reconstruct the full result"
    );
}

#[test]
fn failure_record_preserves_category_and_input_fingerprint() {
    let logger = ReplayLogger::memory(8);
    let ds = common::hand_small();
    let bad_budget = Budget::new(0, 1);
    let err = prepare(&plan(), &ds.left, &ds.right, &bad_budget).unwrap_err();
    assert_eq!(err.code, ErrorCode::InvalidPlan);

    let id = logger.record(iejoin_range::replay::ReplayRecord {
        run_id: iejoin_range::replay::RunId::generate().to_string(),
        outcome: OutcomeKind::Failed,
        plan: plan(),
        budget: iejoin_range::replay::BudgetSnapshot::from(bad_budget),
        left: iejoin_range::replay::BatchFingerprint::of(&ds.left),
        right: iejoin_range::replay::BatchFingerprint::of(&ds.right),
        checkpoint: Default::default(),
        stats: Default::default(),
        truncation: None,
        error: Some(iejoin_range::replay::ErrorSnapshot::from_error(&err)),
        rationale: "failure".into(),
    });
    let rec = logger.get(&id).unwrap();
    assert_eq!(rec.outcome, OutcomeKind::Failed);
    assert_eq!(rec.error.as_ref().unwrap().code, "invalid_plan");
    assert_eq!(rec.error.as_ref().unwrap().category, "input");
}

#[test]
fn run_ids_are_monotonic_and_unique() {
    let a = iejoin_range::replay::RunId::generate();
    let b = iejoin_range::replay::RunId::generate();
    let c = iejoin_range::replay::RunId::generate();
    let ids = [a.to_string(), b.to_string(), c.to_string()];
    assert_ne!(ids[0], ids[1]);
    assert_ne!(ids[1], ids[2]);
    assert!(
        ids[0] < ids[1],
        "lexicographic ordering matches numeric zero-padding: {a} < {b}"
    );
    assert!(ids[1] < ids[2]);
}
