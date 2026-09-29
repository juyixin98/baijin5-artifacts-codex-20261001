//! Failure-category tests: each asserts the SPECIFIC error code AND category,
//! not merely that "the API can be called". Covers input validation, state
//! conflicts, resource exhaustion and (where reachable) compute invariants.

mod common;

use iejoin_range::error::ErrorCategory;
use iejoin_range::operator::{Comparator, JoinPlan, Predicate};
use iejoin_range::resource::Budget;
use iejoin_range::state::{Cursor, SessionRegistry};
use iejoin_range::types::builder::{batch, int_column};
use iejoin_range::validation::{prepare, validate_budget};
use iejoin_range::Checkpoint;
use iejoin_range::{ErrorCode, TypedBatch};

fn plan() -> JoinPlan {
    JoinPlan::new(
        Predicate {
            left_col: 0,
            right_col: 0,
            op: Comparator::Lt,
        },
        Predicate {
            left_col: 1,
            right_col: 1,
            op: Comparator::Lt,
        },
    )
}

fn batches() -> (TypedBatch, TypedBatch) {
    let l = batch(vec![
        int_column("a1", vec![Some(1), Some(2)]),
        int_column("a2", vec![Some(1), Some(2)]),
    ])
    .unwrap();
    let r = batch(vec![
        int_column("b1", vec![Some(3), Some(4)]),
        int_column("b2", vec![Some(3), Some(4)]),
    ])
    .unwrap();
    (l, r)
}

fn assert_code<T: std::fmt::Debug>(
    result: Result<T, iejoin_range::JoinError>,
    code: ErrorCode,
    category: ErrorCategory,
) {
    let err = result.expect_err("expected failure");
    assert_eq!(err.code, code, "wrong code: {err:?}");
    assert_eq!(err.category(), category, "wrong category for {code:?}");
}

// ---- Input validation ----------------------------------------------------

#[test]
fn rejects_out_of_range_column() {
    let (l, r) = batches();
    let bad = JoinPlan::new(
        Predicate {
            left_col: 5,
            right_col: 0,
            op: Comparator::Lt,
        },
        Predicate {
            left_col: 1,
            right_col: 1,
            op: Comparator::Lt,
        },
    );
    assert_code(
        prepare(&bad, &l, &r, &Budget::unlimited()),
        ErrorCode::InvalidPlan,
        ErrorCategory::Input,
    );
}

#[test]
fn rejects_identical_predicate_binding() {
    let (l, r) = batches();
    let bad = JoinPlan::new(
        Predicate {
            left_col: 0,
            right_col: 0,
            op: Comparator::Lt,
        },
        Predicate {
            left_col: 0,
            right_col: 0,
            op: Comparator::Lt,
        },
    );
    assert_code(
        prepare(&bad, &l, &r, &Budget::unlimited()),
        ErrorCode::DuplicateBinding,
        ErrorCategory::Input,
    );
}

#[test]
fn rejects_zero_budget_as_input_not_resource() {
    // A structurally invalid budget is an input error; exceeding a valid one
    // is a resource outcome. These must not be conflated.
    assert_code(
        validate_budget(&Budget::new(0, 10)),
        ErrorCode::InvalidPlan,
        ErrorCategory::Input,
    );
    assert_code(
        validate_budget(&Budget::new(10, 0)),
        ErrorCode::InvalidPlan,
        ErrorCategory::Input,
    );
}

#[test]
fn rejects_batches_with_mismatched_column_lengths() {
    let bad = TypedBatch::try_new(vec![
        int_column("a1", vec![Some(1), Some(2)]),
        int_column("a2", vec![Some(1)]),
    ]);
    assert_code(
        bad.map(|_| ()),
        ErrorCode::ColumnLengthMismatch,
        ErrorCategory::Input,
    );
}

// ---- Resource exhaustion -------------------------------------------------

#[test]
fn output_over_budget_is_distinct_resource_error() {
    let (l, r) = batches();
    // a1 < b1 AND a2 < b2: every left matches every right => 4 pairs.
    let tight = Budget::new(2, u64::MAX);
    let err = iejoin_range::join_full(&plan(), &l, &r, &tight).unwrap_err();
    assert_eq!(err.code, ErrorCode::BudgetExceeded);
    assert_eq!(err.category(), ErrorCategory::Resource);
}

#[test]
fn candidate_budget_is_distinct_from_output_budget() {
    let ds = common::dense_duplicates(4, 4);
    let tight_access = Budget::new(100_000, 1);
    let prepared = prepare(&plan(), &ds.left, &ds.right, &tight_access).unwrap();
    let page = prepared.run_page(&tight_access, Checkpoint::start());
    assert_eq!(page.truncation, iejoin_range::Truncation::CandidateLimit);
    assert_eq!(
        page.stats.candidate_accesses, 1,
        "stops exactly at the ceiling"
    );
    assert!(!page.finished);
}

#[test]
fn session_registry_capacity_is_a_resource_failure() {
    let (l, r) = batches();
    let registry = SessionRegistry::new(1);
    let p1 = prepare(&plan(), &l, &r, &Budget::unlimited()).unwrap();
    let meta = iejoin_range::state::SessionMeta {
        plan: plan(),
        budget: Budget::unlimited(),
        left: iejoin_range::replay::BatchFingerprint::of(&l),
        right: iejoin_range::replay::BatchFingerprint::of(&r),
    };
    registry.create("s1", p1, meta.clone()).unwrap();
    let p2 = prepare(&plan(), &l, &r, &Budget::unlimited()).unwrap();
    let err = registry.create("s2", p2, meta).unwrap_err();
    assert_eq!(err.code, ErrorCode::SessionLimitReached);
    assert_eq!(err.category(), ErrorCategory::Resource);
}

// ---- State conflicts ------------------------------------------------------

#[test]
fn unknown_session_is_state_conflict() {
    let registry = SessionRegistry::new(4);
    let cursor = Cursor {
        session_id: "ghost".into(),
        page_index: 1,
        checkpoint: Checkpoint::start(),
        truncation: iejoin_range::Truncation::OutputLimit,
    };
    assert_code(
        registry.next_page("ghost", &cursor).map(|_| ()),
        ErrorCode::UnknownSession,
        ErrorCategory::State,
    );
}

#[test]
fn cursor_bound_to_other_session_is_mismatch() {
    let (l, r) = batches();
    let registry = SessionRegistry::new(4);
    let prepared = prepare(&plan(), &l, &r, &Budget::unlimited()).unwrap();
    let meta = iejoin_range::state::SessionMeta {
        plan: plan(),
        budget: Budget::unlimited(),
        left: iejoin_range::replay::BatchFingerprint::of(&l),
        right: iejoin_range::replay::BatchFingerprint::of(&r),
    };
    registry.create("real", prepared, meta).unwrap();
    let wrong = Cursor {
        session_id: "other".into(),
        page_index: 1,
        checkpoint: Checkpoint::start(),
        truncation: iejoin_range::Truncation::OutputLimit,
    };
    assert_code(
        registry.next_page("real", &wrong).map(|_| ()),
        ErrorCode::CursorMismatch,
        ErrorCategory::State,
    );
}

#[test]
fn continuing_a_finished_session_is_distinct_from_unknown() {
    let (l, r) = batches();
    let registry = SessionRegistry::new(4);
    let prepared = prepare(&plan(), &l, &r, &Budget::unlimited()).unwrap();
    let meta = iejoin_range::state::SessionMeta {
        plan: plan(),
        budget: Budget::unlimited(),
        left: iejoin_range::replay::BatchFingerprint::of(&l),
        right: iejoin_range::replay::BatchFingerprint::of(&r),
    };
    registry.create("done", prepared, meta).unwrap();
    let first = registry.first_page("done").unwrap();
    assert!(first.finished, "fixture fits one page");
    let cursor = Cursor {
        session_id: "done".into(),
        page_index: 1,
        checkpoint: first.next,
        truncation: iejoin_range::Truncation::Complete,
    };
    assert_code(
        registry.next_page("done", &cursor).map(|_| ()),
        ErrorCode::SessionFinished,
        ErrorCategory::State,
    );
}

#[test]
fn stale_cursor_page_index_is_rejected() {
    let (l, r) = batches();
    let registry = SessionRegistry::new(4);
    // Tiny output budget forces multiple pages.
    let budget = Budget::new(1, u64::MAX);
    let prepared = prepare(&plan(), &l, &r, &budget).unwrap();
    let meta = iejoin_range::state::SessionMeta {
        plan: plan(),
        budget,
        left: iejoin_range::replay::BatchFingerprint::of(&l),
        right: iejoin_range::replay::BatchFingerprint::of(&r),
    };
    registry.create("paged", prepared, meta).unwrap();
    registry.first_page("paged").unwrap();
    // Reuse page_index 0 after page 0 was already delivered.
    let stale = Cursor {
        session_id: "paged".into(),
        page_index: 0,
        checkpoint: Checkpoint::start(),
        truncation: iejoin_range::Truncation::OutputLimit,
    };
    assert_code(
        registry.next_page("paged", &stale).map(|_| ()),
        ErrorCode::CursorMismatch,
        ErrorCategory::State,
    );
}

// ---- Distinguishability summary ------------------------------------------

#[test]
fn all_four_categories_are_distinct() {
    let cats = [
        ErrorCategory::Input,
        ErrorCategory::State,
        ErrorCategory::Resource,
        ErrorCategory::Compute,
    ];
    for w in cats.windows(2) {
        assert_ne!(w[0], w[1]);
    }
}
