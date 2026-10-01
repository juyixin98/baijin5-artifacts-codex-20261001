//! Cursor state, controlled batching and truncation tests.
//!
//! These assert concrete lifecycle behavior: exact-once pagination
//! (including mid-row pauses), unknown/closed cursor state conflicts,
//! page-size validation, the per-cursor batch cap, and that one-shot
//! overflow is reported as `truncated` rather than materializing over
//! budget.

use iejoin::dto::{
    BatchDto, ColumnDto, CursorOpenRequest, CursorPageRequest, JoinRequest, PredicateDto,
};
use iejoin::engine;
use iejoin::error::ErrorCategory;
use iejoin::fixtures as fx;
use iejoin::resource::Budget;
use iejoin::state::SessionStore;
use iejoin::trace::Tracer;

fn tracer() -> Tracer {
    Tracer::new(None)
}

fn all_equal_join_request(budget: Option<Budget>) -> JoinRequest {
    // 6 left x 4 right, <= on both, all keys 1 -> full 24-pair product.
    let make = |n: usize, p: &str| BatchDto {
        columns: vec![
            ColumnDto {
                name: "x".to_owned(),
                values: vec![Some(1); n],
            },
            ColumnDto {
                name: "y".to_owned(),
                values: vec![Some(1); n],
            },
        ],
        row_ids: Some((0..n).map(|i| format!("{p}{i}")).collect()),
    };
    JoinRequest {
        left: make(6, "l"),
        right: make(4, "r"),
        predicates: vec![
            PredicateDto {
                left_column: "x".to_owned(),
                op: "<=".to_owned(),
                right_column: "x".to_owned(),
            },
            PredicateDto {
                left_column: "y".to_owned(),
                op: "<=".to_owned(),
                right_column: "y".to_owned(),
            },
        ],
        budget,
        include_trace: false,
    }
}

#[test]
fn batched_cursor_reassembles_full_multiset_exact_once() {
    let store = SessionStore::new();
    let tr = tracer();
    let parsed = all_equal_join_request(None).parse().unwrap();
    let opened = engine::open_cursor(parsed, Some(5), &store, &tr).unwrap();
    let cursor_id = opened.cursor_id.clone();

    let mut collected = opened.page.pairs.clone();
    let mut pages = 1usize;
    let mut saw_finish = opened.page.finished;
    while !saw_finish {
        let page = engine::advance(cursor_id.clone(), Some(5), &store, &tr).unwrap();
        collected.extend(page.pairs);
        pages += 1;
        saw_finish = page.finished;
    }
    assert_eq!(collected.len(), 24, "24 unique pairs total");
    assert!(
        pages >= 5,
        "paging of 5 across 24 needs >=5 pages, got {pages}"
    );

    // Multiset with multiplicity exactly one each.
    let ms = iejoin::batch::multiset(
        &collected
            .iter()
            .map(|p| iejoin::batch::OutputPair {
                left_id: p.left_id.clone(),
                right_id: p.right_id.clone(),
            })
            .collect::<Vec<_>>(),
    );
    assert_eq!(ms.len(), 24);
    assert!(ms.values().all(|c| *c == 1), "no duplicate or missing pair");

    // Cursor is closed after the final page.
    let e = engine::advance(cursor_id.clone(), Some(5), &store, &tr).unwrap_err();
    assert_eq!(e.category, ErrorCategory::StateConflict);
    assert_eq!(e.code, "unknown_cursor");
}

#[test]
fn unknown_cursor_is_state_conflict() {
    let store = SessionStore::new();
    let tr = tracer();
    let e = engine::advance("cur-does-not-exist".to_owned(), Some(10), &store, &tr).unwrap_err();
    assert_eq!(e.category, ErrorCategory::StateConflict);
    assert_eq!(e.code, "unknown_cursor");
}

#[test]
fn zero_page_size_is_input_error() {
    let store = SessionStore::new();
    let tr = tracer();
    let parsed = all_equal_join_request(None).parse().unwrap();
    let e = engine::open_cursor(parsed, Some(0), &store, &tr).unwrap_err();
    assert_eq!(e.category, ErrorCategory::Input);
    assert_eq!(e.code, "invalid_page_size");
}

#[test]
fn page_size_above_budget_is_input_error() {
    let store = SessionStore::new();
    let tr = tracer();
    let budget = Budget {
        max_output_pairs: 10,
        ..Budget::default()
    };
    let parsed = all_equal_join_request(Some(budget)).parse().unwrap();
    let e = engine::open_cursor(parsed, Some(11), &store, &tr).unwrap_err();
    assert_eq!(e.category, ErrorCategory::Input);
    assert_eq!(e.code, "invalid_page_size");
}

#[test]
fn per_cursor_batch_cap_is_resource_exhausted() {
    let store = SessionStore::new();
    let tr = tracer();
    let budget = Budget {
        max_output_pairs: 100,
        max_batches_per_cursor: 3,
        ..Budget::default()
    };
    let parsed = all_equal_join_request(Some(budget)).parse().unwrap();
    let opened = engine::open_cursor(parsed, Some(1), &store, &tr).unwrap();
    let id = opened.cursor_id.clone();

    // Open consumed page 1 (batches_yielded=1). Two more advances yield
    // pages 2 and 3; the next advance hits the cap of 3 and is rejected
    // before the 24-pair product completes.
    engine::advance(id.clone(), Some(1), &store, &tr).unwrap();
    engine::advance(id.clone(), Some(1), &store, &tr).unwrap();
    let capped = engine::advance(id.clone(), Some(1), &store, &tr).unwrap_err();
    assert_eq!(capped.category, ErrorCategory::ResourceExhausted);
    assert_eq!(capped.code, "cursor_batch_limit");
}

#[test]
fn one_shot_truncation_is_explicit() {
    let tr = tracer();
    let budget = Budget {
        max_output_pairs: 7,
        ..Budget::default()
    };
    let parsed = all_equal_join_request(Some(budget)).parse().unwrap();
    let out = engine::execute_oneshot(parsed, &tr).unwrap();
    assert!(out.truncated, "24 matches over cap 7 must truncate");
    assert_eq!(out.pairs.len(), 7);
}

#[test]
fn one_shot_within_budget_is_not_truncated() {
    let tr = tracer();
    // Selective scenario: exactly 5 pairs.
    let sc = fx::by_name("selective").unwrap();
    let req = JoinRequest {
        left: fx::batch_to_dto(&sc.left),
        right: fx::batch_to_dto(&sc.right),
        predicates: vec![
            PredicateDto {
                left_column: "x".to_owned(),
                op: "<".to_owned(),
                right_column: "x".to_owned(),
            },
            PredicateDto {
                left_column: "y".to_owned(),
                op: "<".to_owned(),
                right_column: "y".to_owned(),
            },
        ],
        budget: None,
        include_trace: false,
    };
    let out = engine::execute_oneshot(req.parse().unwrap(), &tr).unwrap();
    assert!(!out.truncated);
    assert_eq!(out.pairs.len(), 5);
}

#[test]
fn open_via_wire_dto_round_trip() {
    // Ensures the cursor open DTO flattens the join request correctly.
    let store = SessionStore::new();
    let tr = tracer();
    let open_req = CursorOpenRequest {
        join: all_equal_join_request(None),
        page_size: Some(10),
    };
    let json_bytes = serde_json::to_vec(&open_req).unwrap();
    let parsed_open: CursorOpenRequest = serde_json::from_slice(&json_bytes).unwrap();
    let opened = engine::open_cursor(
        parsed_open.join.parse().unwrap(),
        parsed_open.page_size,
        &store,
        &tr,
    )
    .unwrap();
    assert_eq!(opened.page.count, 10);
    assert!(!opened.page.finished);

    // next page via wire DTO
    let next_req = CursorPageRequest {
        cursor_id: opened.cursor_id.clone(),
        page_size: Some(10),
    };
    let bytes = serde_json::to_vec(&next_req).unwrap();
    let parsed_next: CursorPageRequest = serde_json::from_slice(&bytes).unwrap();
    let page = engine::advance(parsed_next.cursor_id, parsed_next.page_size, &store, &tr).unwrap();
    assert_eq!(page.count, 10);
}
