//! Validation tests assert the *specific* failure category and machine code,
//! not merely "the call failed".
mod common;

use common::*;
use leapfrog_triejoin::domain::{Datum, LogicalType, NullPolicy};
use leapfrog_triejoin::error::ErrorCode;
use leapfrog_triejoin::query::JoinRequest;
use leapfrog_triejoin::{compile, resolve_inputs, AppState, Catalog, ServerConfig};

fn compile_result(
    mut request: JoinRequest,
) -> Result<leapfrog_triejoin::Plan, leapfrog_triejoin::JoinError> {
    let state = AppState::new(ServerConfig::default(), Catalog::new());
    let inputs = resolve_inputs(&request, state.catalog.try_read().unwrap().entries())?;
    request.limit = request.effective_limit();
    compile(inputs, &request)
}

#[test]
fn rejects_disconnected_join_graph_as_cartesian_product() {
    // ab(x,y) and cd(z,w) share nothing -> would be a Cartesian product.
    let r1 = rel(
        "ab",
        &[("x", LogicalType::Int64), ("y", LogicalType::Int64)],
        vec![ints(&[1, 2])],
    );
    let r2 = rel(
        "cd",
        &[("z", LogicalType::Int64), ("w", LogicalType::Int64)],
        vec![ints(&[3, 4])],
    );
    let err = compile_result(req(vec![r1, r2])).unwrap_err();
    assert_eq!(err.code, ErrorCode::DisconnectedJoinGraph);
}

#[test]
fn rejects_chain_with_a_dangling_relation() {
    // ab ⋈ bc are connected, but dz is disconnected -> product with it.
    let ab = rel(
        "ab",
        &[("a", LogicalType::Int64), ("b", LogicalType::Int64)],
        vec![ints(&[1, 2])],
    );
    let bc = rel(
        "bc",
        &[("b", LogicalType::Int64), ("c", LogicalType::Int64)],
        vec![ints(&[2, 3])],
    );
    let dz = rel(
        "dz",
        &[("d", LogicalType::Int64), ("z", LogicalType::Int64)],
        vec![ints(&[9, 8])],
    );
    let err = compile_result(req(vec![ab, bc, dz])).unwrap_err();
    assert_eq!(err.code, ErrorCode::DisconnectedJoinGraph);
}

#[test]
fn rejects_type_mismatch_on_shared_attribute() {
    let r1 = rel("l", &[("k", LogicalType::Int64)], vec![vec![Datum::Int(1)]]);
    let r2 = rel(
        "r",
        &[("k", LogicalType::Utf8)],
        vec![vec![Datum::Str("1".to_string())]],
    );
    let err = compile_result(req(vec![r1, r2])).unwrap_err();
    assert_eq!(err.code, ErrorCode::TypeMismatch);
}

#[test]
fn rejects_null_in_join_key_under_default_policy() {
    let r1 = rel(
        "l",
        &[("k", LogicalType::Int64), ("y", LogicalType::Int64)],
        vec![vec![Datum::Null, Datum::Int(1)]],
    );
    let r2 = rel(
        "r",
        &[("k", LogicalType::Int64), ("z", LogicalType::Int64)],
        vec![vec![Datum::Int(1), Datum::Int(2)]],
    );
    let err = compile_result(req(vec![r1, r2])).unwrap_err();
    assert_eq!(err.code, ErrorCode::NullInJoinKey);
    assert_eq!(err.category(), leapfrog_triejoin::ErrorCategory::Validation);
}

#[test]
fn drop_join_rows_policy_excludes_and_counts() {
    let r1 = rel(
        "l",
        &[("k", LogicalType::Int64), ("y", LogicalType::Int64)],
        vec![
            vec![Datum::Null, Datum::Int(1)],
            vec![Datum::Int(5), Datum::Int(1)],
        ],
    );
    let r2 = rel(
        "r",
        &[("k", LogicalType::Int64), ("z", LogicalType::Int64)],
        vec![vec![Datum::Int(5), Datum::Int(2)]],
    );
    let mut request = req(vec![r1, r2]);
    request.null_policy = NullPolicy::DropJoinRows;
    let case = compile_case(request);
    assert_eq!(case.plan.relations[0].null_join_rows_dropped, 1);
    let out = engine_rows(&case, 100, None);
    assert_eq!(out.rows.len(), 1);
    assert_eq!(as_ints(&out.rows[0].values), vec![5, 1, 2]);
}

#[test]
fn rejects_row_arity_mismatch() {
    let r1 = rel(
        "l",
        &[("k", LogicalType::Int64), ("y", LogicalType::Int64)],
        vec![vec![Datum::Int(1)]], // too few
    );
    let r2 = rel("r", &[("k", LogicalType::Int64)], vec![vec![Datum::Int(1)]]);
    let err = compile_result(req(vec![r1, r2])).unwrap_err();
    assert_eq!(err.code, ErrorCode::RowArityMismatch);
}

#[test]
fn rejects_unbound_select_attribute() {
    let r1 = rel("l", &[("k", LogicalType::Int64)], vec![vec![Datum::Int(1)]]);
    let mut request = req(vec![r1]);
    request.select = Some(vec!["nope".to_string()]);
    let err = compile_result(request).unwrap_err();
    assert_eq!(err.code, ErrorCode::VariableNotBound);
}

#[test]
fn rejects_unknown_fixture() {
    let mut request = req(vec![]);
    request.fixtures = vec!["does_not_exist".to_string()];
    let state = AppState::new(ServerConfig::default(), Catalog::new());
    let err = resolve_inputs(&request, state.catalog.try_read().unwrap().entries()).unwrap_err();
    assert_eq!(err.code, ErrorCode::MissingRelation);
}

#[test]
fn rejects_empty_relation_list() {
    let err = compile_result(req(vec![])).unwrap_err();
    assert_eq!(err.code, ErrorCode::EmptyRelationList);
}

#[test]
fn rejects_duplicate_column_in_schema() {
    let r = rel(
        "t",
        &[("k", LogicalType::Int64), ("k", LogicalType::Int64)],
        vec![vec![Datum::Int(1), Datum::Int(2)]],
    );
    let err = compile_result(req(vec![r])).unwrap_err();
    assert_eq!(err.code, ErrorCode::DuplicateColumn);
}

#[test]
fn rejects_excessive_limit() {
    let r = rel("t", &[("k", LogicalType::Int64)], vec![vec![Datum::Int(1)]]);
    let mut request = req(vec![r]);
    request.limit = 2_000_000;
    let err = compile_result(request).unwrap_err();
    assert_eq!(err.code, ErrorCode::BadLimit);
}
