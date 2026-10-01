//! Assert concrete, categorized failures — not merely "the API can be
//! called". Each test pins the error category AND code, exercising the
//! distinction between input errors, state conflicts, resource
//! exhaustion and compute failures.

use iejoin::dto::{BatchDto, ColumnDto, JoinRequest, PredicateDto};
use iejoin::error::ErrorCategory;
use iejoin::resource::Budget;

fn col(name: &str, values: Vec<Option<i64>>) -> ColumnDto {
    ColumnDto {
        name: name.to_owned(),
        values,
    }
}

fn batch2(x: Vec<Option<i64>>, y: Vec<Option<i64>>) -> BatchDto {
    BatchDto {
        columns: vec![col("x", x), col("y", y)],
        row_ids: None,
    }
}

fn request_with(preds: Vec<PredicateDto>) -> JoinRequest {
    JoinRequest {
        left: batch2(vec![Some(1)], vec![Some(1)]),
        right: batch2(vec![Some(2)], vec![Some(2)]),
        predicates: preds,
        budget: None,
        include_trace: false,
    }
}

fn pred(l: &str, op: &str, r: &str) -> PredicateDto {
    PredicateDto {
        left_column: l.to_owned(),
        op: op.to_owned(),
        right_column: r.to_owned(),
    }
}

#[test]
fn wrong_predicate_count_is_input_error() {
    let e = request_with(vec![pred("x", "<", "x")]).parse().unwrap_err();
    assert_eq!(e.category, ErrorCategory::Input);
    assert_eq!(e.code, "predicate_count");
}

#[test]
fn three_predicates_is_input_error() {
    let e = request_with(vec![
        pred("x", "<", "x"),
        pred("y", "<", "y"),
        pred("z", "<", "z"),
    ])
    .parse()
    .unwrap_err();
    assert_eq!(e.category, ErrorCategory::Input);
    assert_eq!(e.code, "predicate_count");
}

#[test]
fn bad_comparator_symbol_is_input_error() {
    let e = request_with(vec![pred("x", "=", "x"), pred("y", "<", "y")])
        .parse()
        .unwrap_err();
    assert_eq!(e.category, ErrorCategory::Input);
    assert_eq!(e.code, "unsupported_comparator");
}

#[test]
fn unknown_column_is_input_error() {
    // Column binding happens at engine prepare time (parse checks
    // shape/syntax only), but the category is still input.
    let req = request_with(vec![pred("nope", "<", "x"), pred("y", "<", "y")]);
    let parsed = req.parse().unwrap();
    let tracer = iejoin::trace::Tracer::new(None);
    let e = iejoin::engine::execute_oneshot(parsed, &tracer).unwrap_err();
    assert_eq!(e.category, ErrorCategory::Input);
    assert_eq!(e.code, "unknown_column");
}

#[test]
fn same_column_in_both_predicates_is_input_error() {
    let e = request_with(vec![pred("x", "<", "x"), pred("x", "<", "y")])
        .parse()
        .unwrap_err();
    assert_eq!(e.category, ErrorCategory::Input);
    assert_eq!(e.code, "duplicate_predicate_column");
}

#[test]
fn unequal_column_lengths_is_input_error() {
    let bad = BatchDto {
        columns: vec![col("x", vec![Some(1), Some(2)]), col("y", vec![Some(1)])],
        row_ids: None,
    };
    let mut req = request_with(vec![pred("x", "<", "x"), pred("y", "<", "y")]);
    req.left = bad;
    let e = req.parse().unwrap_err();
    assert_eq!(e.category, ErrorCategory::Input);
    assert_eq!(e.code, "column_length_mismatch");
}

#[test]
fn empty_schema_is_input_error() {
    let mut req = request_with(vec![pred("x", "<", "x"), pred("y", "<", "y")]);
    req.left = BatchDto {
        columns: vec![],
        row_ids: None,
    };
    let e = req.parse().unwrap_err();
    assert_eq!(e.category, ErrorCategory::Input);
    assert_eq!(e.code, "empty_schema");
}

#[test]
fn duplicate_row_ids_is_input_error() {
    let mut req = request_with(vec![pred("x", "<", "x"), pred("y", "<", "y")]);
    req.left.row_ids = Some(vec!["dup".to_owned(), "dup".to_owned()]);
    req.left.columns = vec![
        col("x", vec![Some(1), Some(1)]),
        col("y", vec![Some(1), Some(1)]),
    ];
    let e = req.parse().unwrap_err();
    assert_eq!(e.category, ErrorCategory::Input);
    assert_eq!(e.code, "duplicate_row_id");
}

#[test]
fn row_id_count_mismatch_is_input_error() {
    let mut req = request_with(vec![pred("x", "<", "x"), pred("y", "<", "y")]);
    // Two rows on both columns, but only one row id supplied.
    req.left = batch2(vec![Some(1), Some(2)], vec![Some(1), Some(2)]);
    req.left.row_ids = Some(vec!["only".to_owned()]);
    let e = req.parse().unwrap_err();
    assert_eq!(e.category, ErrorCategory::Input);
    assert_eq!(e.code, "row_id_count_mismatch");
}

#[test]
fn oversized_input_is_resource_exhausted_not_input() {
    let budget = Budget {
        max_input_rows: 2,
        ..Budget::default()
    };
    let mut req = request_with(vec![pred("x", "<", "x"), pred("y", "<", "y")]);
    req.budget = Some(budget);
    req.left = batch2(
        vec![Some(1), Some(2), Some(3)],
        vec![Some(1), Some(2), Some(3)],
    );
    let e = req.parse().unwrap_err();
    assert_eq!(e.category, ErrorCategory::ResourceExhausted);
    assert_eq!(e.code, "input_too_large");
    // Structured details are populated.
    assert_eq!(e.details.get("side").and_then(|v| v.as_str()), Some("left"));
    assert_eq!(e.details.get("limit").and_then(|v| v.as_u64()), Some(2));
}

#[test]
fn error_categories_map_to_distinct_http_status() {
    assert_eq!(ErrorCategory::Input.http_status(), 400);
    assert_eq!(ErrorCategory::StateConflict.http_status(), 409);
    assert_eq!(ErrorCategory::ResourceExhausted.http_status(), 413);
    assert_eq!(ErrorCategory::Compute.http_status(), 500);
}
