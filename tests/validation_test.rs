//! Validation entry-point tests: malformed plans/data must fail with a
//! concrete failure category and message — never with a generic success.

mod common;

use common::CaseBuilder;
use recursive_cte_backend::plan::{validate_request, RecursiveRequest};
use recursive_cte_backend::{execute, FailureCategory};
use serde_json::{json, Value};

fn validate_err(req: &RecursiveRequest) -> recursive_cte_backend::EngineError {
    validate_request(req).expect_err("expected a validation error")
}

fn execute_err(req: &RecursiveRequest) -> recursive_cte_backend::EngineError {
    execute(req, "itest-validate").expect_err("expected execution to fail")
}

#[test]
fn rejects_empty_seed() {
    let mut req = CaseBuilder::new("empty-seed", &[(1, 2)]).build();
    req.view.rows = Vec::new();
    let err = validate_err(&req);
    assert_eq!(err.category, FailureCategory::InvalidPlan);
    assert!(err.message.contains("seed"));
}

#[test]
fn rejects_missing_edge_relation() {
    let mut req = CaseBuilder::new("missing-rel", &[(1, 2)]).build();
    req.relations.clear();
    let err = validate_err(&req);
    assert_eq!(err.category, FailureCategory::InvalidPlan);
    assert!(err.message.contains("not declared"));
}

#[test]
fn rejects_empty_join_keys() {
    let mut req = CaseBuilder::new("no-join", &[(1, 2)]).build();
    req.recursive_term.on.clear();
    let err = validate_err(&req);
    assert_eq!(err.category, FailureCategory::InvalidPlan);
    assert!(err.message.contains("equi-join"));
}

#[test]
fn rejects_unknown_join_column() {
    let mut req = CaseBuilder::new("bad-join", &[(1, 2)]).build();
    req.recursive_term.on[0].edge = "ghost".to_string();
    let err = validate_err(&req);
    assert_eq!(err.category, FailureCategory::InvalidPlan);
    assert!(err.message.contains("ghost"));
}

#[test]
fn rejects_projection_of_managed_path_column() {
    let mut req = CaseBuilder::new("managed-proj", &[(1, 2)]).build();
    req.recursive_term.project.push(
        serde_json::from_value(json!({
            "as": common::PATH,
            "expr": "recursive_column",
            "name": common::PATH,
        }))
        .unwrap(),
    );
    let err = validate_err(&req);
    assert_eq!(err.category, FailureCategory::InvalidPlan);
    assert!(err.message.contains("engine-managed"));
}

#[test]
fn rejects_missing_projection_for_user_column() {
    let mut req = CaseBuilder::new("missing-proj", &[(1, 2)]).build();
    req.recursive_term
        .project
        .retain(|p| p.alias != common::DEPTH);
    let err = validate_err(&req);
    assert_eq!(err.category, FailureCategory::InvalidPlan);
    assert!(err.message.contains("missing output column"));
}

#[test]
fn rejects_duplicate_projection_alias() {
    let mut req = CaseBuilder::new("dup-proj", &[(1, 2)]).build();
    req.recursive_term.project[1].alias = common::NODE.to_string();
    let err = validate_err(&req);
    assert_eq!(err.category, FailureCategory::InvalidPlan);
    assert!(err.message.contains("duplicate projection"));
}

#[test]
fn rejects_non_keyable_cycle_column() {
    let mut req = CaseBuilder::new("bool-key", &[(1, 2)]).build();
    // Redeclare the key column as bool (and align seed/projection types loosely
    // — validation should fail first on the key-type rule).
    req.view.columns[0].data_type = recursive_cte_backend::plan::ColumnType::Bool;
    // Seed rows still ints; the key-type rule is checked before type parsing.
    let err = validate_err(&req);
    assert_eq!(err.category, FailureCategory::InvalidPlan);
    assert!(err.message.contains("int64 or utf8"));
}

#[test]
fn rejects_path_column_wrong_list_type() {
    let mut req = CaseBuilder::new("bad-path-type", &[(1, 2)]).build();
    req.view.columns[2].data_type = recursive_cte_backend::plan::ColumnType::ListUtf8;
    let err = validate_err(&req);
    assert_eq!(err.category, FailureCategory::InvalidPlan);
    assert!(err.message.contains("must have type"));
}

#[test]
fn rejects_seed_row_wrong_arity() {
    let mut req = CaseBuilder::new("arity", &[(1, 2)]).build();
    req.view.rows[0].push(json!(99));
    let err = validate_err(&req);
    assert_eq!(err.category, FailureCategory::InvalidData);
    assert!(err.message.contains("values"));
}

#[test]
fn rejects_typed_data_mismatch_in_edges() {
    let mut req = CaseBuilder::new("bad-edge-value", &[(1, 2)]).build();
    req.relations.get_mut("edges").unwrap().rows[0][1] = json!("not-an-int");
    let err = execute_err(&req);
    assert_eq!(err.category, FailureCategory::InvalidData);
    assert!(err.message.contains("expected value of type int64"));
}

#[test]
fn rejects_null_cycle_key_in_seed() {
    let mut req = CaseBuilder::new("null-key", &[(1, 2)]).build();
    req.view.rows[0][0] = Value::Null;
    let err = execute_err(&req);
    assert_eq!(err.category, FailureCategory::InvalidData);
    assert!(err.message.contains("must not be null"));
}

#[test]
fn rejects_type_mismatched_projection_output() {
    // Project the edge destination (int) into the depth column but force the
    // column to utf8 via an expression yielding a string: pass an edge string
    // literal into an int64 column instead.
    let mut req = CaseBuilder::new("bad-proj-type", &[(1, 2)]).build();
    req.recursive_term.project[1].value =
        serde_json::from_value(json!({ "expr": "literal", "value": "oops" })).unwrap();
    let err = execute_err(&req);
    assert_eq!(err.category, FailureCategory::InvalidData);
    assert!(err.message.contains("recursive projection"));
}

#[test]
fn integer_overflow_is_an_explicit_failure() {
    // Two edges so the second expansion adds i64::MAX to an already-large depth
    // (the first expansion yields 0 + MAX = MAX, which is not itself overflow).
    let mut req = CaseBuilder::new("overflow", &[(1, 2), (2, 3)]).build();
    req.recursive_term.project[1].value = serde_json::from_value(json!({
        "expr": "add",
        "left": { "expr": "recursive_column", "name": common::DEPTH },
        "right": { "expr": "literal", "value": i64::MAX }
    }))
    .unwrap();
    let err = execute_err(&req);
    assert_eq!(err.category, FailureCategory::InvalidData);
    assert!(err.message.contains("overflow"));
}

#[test]
fn a_valid_request_validates_and_executes() {
    let req = CaseBuilder::new("happy", &[(1, 2)]).build();
    validate_request(&req).expect("valid request must validate");
    let resp = execute(&req, "itest-happy").expect("execution must succeed");
    assert_eq!(
        resp.status,
        recursive_cte_backend::plan::RunStatus::Complete
    );
}
