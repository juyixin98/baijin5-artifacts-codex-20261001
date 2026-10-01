//! Shared test fixtures and helpers.
//!
//! Every test drives the public pipeline (validation + executor), never
//! private internals. Expected results are hand-written here; the separate
//! reference oracle supplies independent cross-checks.

#![allow(dead_code)]

use recursive_cte::api::response::ExecuteResponse;
use recursive_cte::api::service::run_pipeline;
use recursive_cte::api::types::ExecuteRequest;
use recursive_cte::api::validate::validate_request;
use recursive_cte::batch::RecordBatch;
use recursive_cte::config::Config;
use recursive_cte::executor;
use recursive_cte::state::RunRecord;

/// Parse a JSON-value request into the API input type.
pub fn parse_req(json: serde_json::Value) -> ExecuteRequest {
    serde_json::from_value(json).expect("fixture request must deserialize")
}

/// Run the full HTTP-facing pipeline on a JSON request.
pub fn run_json(json: serde_json::Value, config: &Config) -> ExecuteResponse {
    let req = parse_req(json);
    run_pipeline(req, config).expect("fixture request must validate and execute")
}

/// Run the typed executor directly (used for batch-level assertions).
pub fn execute_json(json: serde_json::Value, config: &Config) -> (RecordBatch, RunRecord) {
    let req = parse_req(json);
    let validated = validate_request(req, config).expect("fixture request must validate");
    executor::execute(&validated.plan, validated.limits).expect("execution must succeed")
}

/// A standard single-column `id int64` graph request.
pub fn id_graph_request(
    seeds: &[i64],
    edges: &[(i64, i64)],
    union_op: &str,
    order: &str,
) -> serde_json::Value {
    serde_json::json!({
        "cte_name": "nodes",
        "union": union_op,
        "order": order,
        "schema": [{"name": "id", "type": "int64"}],
        "key_columns": ["id"],
        "seed": seeds.iter().map(|n| serde_json::json!([n])).collect::<Vec<_>>(),
        "edges": {
            "schema": [
                {"name": "from_id", "type": "int64"},
                {"name": "to_id", "type": "int64"}
            ],
            "rows": edges
                .iter()
                .map(|(f, t)| serde_json::json!([f, t]))
                .collect::<Vec<_>>()
        },
        "recursive": {
            "parent_key": "id",
            "edge_from": "from_id",
            "projection": [{"edge_column": "to_id"}]
        }
    })
}

/// Pull `(business id, rendered path, cycle)` triples out of an id-graph run.
pub fn id_triples(resp: &ExecuteResponse) -> Vec<(i64, String, bool)> {
    resp.rows
        .iter()
        .map(|row| {
            (
                row[0].as_i64().expect("id is int64"),
                row[1].as_str().expect("path is utf8").to_owned(),
                row[2].as_bool().expect("cycle is boolean"),
            )
        })
        .collect()
}

/// Render an integer key path the same way the engine renders its path col.
pub fn int_path(path: &[i64]) -> String {
    let body = path
        .iter()
        .map(|n| n.to_string())
        .collect::<Vec<_>>()
        .join(",");
    format!("[{body}]")
}
