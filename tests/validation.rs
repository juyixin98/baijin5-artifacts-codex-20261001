//! Validation-entry tests: each malformed request must produce a concrete
//! `validation_error` category — never a silent success or an unknown state.

mod common;

use common::{id_graph_request, parse_req};
use recursive_cte::api::service::{error_response, run_pipeline};
use recursive_cte::config::Config;
use recursive_cte::error::EngineError;

fn expect_validation(json: serde_json::Value, cfg: &Config) -> String {
    let req = parse_req(json);
    let err = run_pipeline(req, cfg).expect_err("request must be rejected");
    let (code, envelope) = error_response(&err);
    assert_eq!(code, 400, "validation failures map to HTTP 400");
    assert_eq!(envelope.outcome, "error");
    assert_eq!(envelope.category, "validation_error");
    assert!(!envelope.message.is_empty());
    match err {
        EngineError::Validation(msg) => msg,
        EngineError::Internal(msg) => panic!("expected validation error, got internal: {msg}"),
    }
}

#[test]
fn rejects_unknown_union_keyword() {
    let mut req = id_graph_request(&[1], &[(1, 2)], "UNION", "bfs");
    req["union"] = "MAYBE".into();
    let msg = expect_validation(req, &Config::default());
    assert!(
        msg.contains("'MAYBE'"),
        "message names the bad value: {msg}"
    );
}

#[test]
fn rejects_unknown_traversal_order() {
    let req = id_graph_request(&[1], &[(1, 2)], "ALL", "sideways");
    let msg = expect_validation(req, &Config::default());
    assert!(msg.contains("sideways"));
}

#[test]
fn rejects_key_column_not_in_schema() {
    let mut req = id_graph_request(&[1], &[(1, 2)], "ALL", "bfs");
    req["key_columns"] = serde_json::json!(["nope"]);
    let msg = expect_validation(req, &Config::default());
    assert!(msg.contains("nope"));
}

#[test]
fn rejects_empty_key_columns() {
    let mut req = id_graph_request(&[1], &[(1, 2)], "ALL", "bfs");
    req["key_columns"] = serde_json::json!([]);
    expect_validation(req, &Config::default());
}

#[test]
fn rejects_wrongly_typed_seed_value() {
    let mut req = id_graph_request(&[1], &[(1, 2)], "ALL", "bfs");
    req["seed"] = serde_json::json!([["not-an-int"]]);
    let msg = expect_validation(req, &Config::default());
    assert!(msg.contains("row 0"));
    assert!(msg.contains("'id'"));
}

#[test]
fn rejects_row_arity_mismatch() {
    let mut req = id_graph_request(&[1], &[(1, 2)], "ALL", "bfs");
    req["seed"] = serde_json::json!([[1, 2]]);
    let msg = expect_validation(req, &Config::default());
    assert!(msg.contains("2 values"));
}

#[test]
fn rejects_float_into_int64() {
    let mut req = id_graph_request(&[1], &[(1, 2)], "ALL", "bfs");
    req["seed"] = serde_json::json!([[1.5]]);
    expect_validation(req, &Config::default());
}

#[test]
fn rejects_unknown_edge_column_in_projection() {
    let mut req = id_graph_request(&[1], &[(1, 2)], "ALL", "bfs");
    req["recursive"]["projection"] = serde_json::json!([{"edge_column": "ghost"}]);
    let msg = expect_validation(req, &Config::default());
    assert!(msg.contains("ghost"));
}

#[test]
fn rejects_projection_arity_mismatch() {
    let mut req = id_graph_request(&[1], &[(1, 2)], "ALL", "bfs");
    req["recursive"]["projection"] = serde_json::json!([]);
    expect_validation(req, &Config::default());
}

#[test]
fn rejects_literal_for_parent_key_column() {
    // A two-column CTE where the join-key column would be fed a constant.
    let req = serde_json::json!({
        "cte_name": "x",
        "union": "ALL",
        "order": "bfs",
        "schema": [
            {"name": "id", "type": "int64"},
            {"name": "tag", "type": "utf8"}
        ],
        "key_columns": ["id"],
        "seed": [[1, "s"]],
        "edges": {
            "schema": [
                {"name": "from_id", "type": "int64"},
                {"name": "to_tag", "type": "utf8"}
            ],
            "rows": [[1, "t"]]
        },
        "recursive": {
            "parent_key": "id",
            "edge_from": "from_id",
            "projection": [
                {"literal": 99},
                {"edge_column": "to_tag"}
            ]
        }
    });
    let msg = expect_validation(req, &Config::default());
    assert!(
        msg.contains("parent key"),
        "message explains the rule: {msg}"
    );
}

#[test]
fn rejects_join_key_type_mismatch() {
    let req = serde_json::json!({
        "cte_name": "x",
        "union": "ALL",
        "order": "bfs",
        "schema": [{"name": "id", "type": "int64"}],
        "key_columns": ["id"],
        "seed": [[1]],
        "edges": {
            "schema": [
                {"name": "from_id", "type": "utf8"},
                {"name": "to_id", "type": "int64"}
            ],
            "rows": [["1", 2]]
        },
        "recursive": {
            "parent_key": "id",
            "edge_from": "from_id",
            "projection": [{"edge_column": "to_id"}]
        }
    });
    let msg = expect_validation(req, &Config::default());
    assert!(msg.contains("join key type mismatch"));
}

#[test]
fn rejects_auxiliary_column_name_collision() {
    let mut req = id_graph_request(&[1], &[(1, 2)], "ALL", "bfs");
    req["path_column"] = serde_json::json!("id");
    expect_validation(req, &Config::default());

    let mut req = id_graph_request(&[1], &[(1, 2)], "ALL", "bfs");
    req["path_column"] = serde_json::json!("p");
    req["cycle_column"] = serde_json::json!("p");
    expect_validation(req, &Config::default());
}

#[test]
fn rejects_duplicate_key_column_listing() {
    let mut req = id_graph_request(&[1], &[(1, 2)], "ALL", "bfs");
    req["key_columns"] = serde_json::json!(["id", "id"]);
    let msg = expect_validation(req, &Config::default());
    assert!(msg.contains("more than once"));
}

#[test]
fn rejects_limit_above_process_ceiling() {
    let cfg = Config::default(); // max_depth 64, max_rows 100000
    let mut req = id_graph_request(&[1], &[(1, 2)], "ALL", "bfs");
    req["limits"] = serde_json::json!({"max_depth": 65});
    let msg = expect_validation(req, &cfg);
    assert!(msg.contains("exceeds process ceiling"));

    let mut req = id_graph_request(&[1], &[(1, 2)], "ALL", "bfs");
    req["limits"] = serde_json::json!({"max_rows": 100001});
    expect_validation(req, &cfg);
}

#[test]
fn rejects_zero_limits() {
    let mut req = id_graph_request(&[1], &[(1, 2)], "ALL", "bfs");
    req["limits"] = serde_json::json!({"max_depth": 0});
    expect_validation(req, &Config::default());
}

#[test]
fn rejects_unknown_json_fields() {
    let err = serde_json::from_str::<recursive_cte::api::ExecuteRequest>(
        r#"{
            "cte_name": "x",
            "schema": [{"name": "id", "type": "int64"}],
            "key_columns": ["id"],
            "edges": {"schema": [{"name": "f", "type": "int64"}, {"name": "t", "type": "int64"}], "rows": []},
            "recursive": {"parent_key": "id", "edge_from": "f", "projection": [{"edge_column": "t"}]},
            "surprise": 42
        }"#,
    )
    .expect_err("unknown field must fail deserialization");
    assert!(err.to_string().contains("surprise"));
}
