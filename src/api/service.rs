//! Execution pipeline: validated request -> engine run -> response DTO.

use crate::api::response::{ExecuteResponse, TraceDto};
use crate::api::types::ExecuteRequest;
use crate::api::validate::validate_request;
use crate::batch::Value;
use crate::config::Config;
use crate::error::Result;
use crate::executor;

use super::response::ErrorResponse;

/// Run a decoded request end to end.
pub fn run_pipeline(req: ExecuteRequest, config: &Config) -> Result<ExecuteResponse> {
    let validated = validate_request(req, config)?;
    let (batch, record) = executor::execute(&validated.plan, validated.limits)?;

    let columns: Vec<String> = batch
        .schema()
        .fields()
        .iter()
        .map(|f| f.name.clone())
        .collect();
    let rows = batch
        .to_rows()
        .into_iter()
        .map(|row| row.into_iter().map(value_to_json).collect())
        .collect();

    Ok(ExecuteResponse {
        outcome: "ok",
        run_id: record.run_id,
        engine_version: record.engine_version,
        cte_name: record.cte_name,
        union_op: record.union_op.as_str().to_owned(),
        order: validated.plan.traversal.as_str().to_owned(),
        status: record.status.as_str().to_owned(),
        complete: record.status.is_complete(),
        columns,
        rows,
        row_count: record.rows_emitted,
        rows_deduplicated: record.rows_deduplicated,
        max_depth_reached: record.max_depth_reached,
        max_depth: record.max_depth,
        max_rows: record.max_rows,
        rounds: record.rounds,
        trace: record
            .trace
            .into_iter()
            .map(|t| TraceDto {
                round: t.round,
                depth: t.depth,
                consumed: t.consumed,
                produced: t.produced,
                cycles: t.cycles,
                duplicates: t.duplicates,
                admitted: t.admitted,
                basis: t.basis,
            })
            .collect(),
    })
}

/// Build the error envelope returned for validation/internal failures.
pub fn error_response(err: &crate::error::EngineError) -> (u16, ErrorResponse) {
    let (status, category) = match err {
        crate::error::EngineError::Validation(_) => (400, "validation_error"),
        crate::error::EngineError::Internal(_) => (500, "internal_error"),
    };
    (
        status,
        ErrorResponse {
            outcome: "error",
            category: category.to_owned(),
            message: err.to_string(),
            run_id: None,
        },
    )
}

fn value_to_json(value: Value) -> serde_json::Value {
    match value {
        Value::Null => serde_json::Value::Null,
        Value::Int64(n) => serde_json::Number::from(n).into(),
        Value::Utf8(s) => serde_json::Value::String(s),
        Value::Boolean(b) => serde_json::Value::Bool(b),
    }
}
