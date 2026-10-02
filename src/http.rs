//! Axum application wiring, kept in the library so integration tests can drive
//! the router in-process with `tower::oneshot` (no open port required).
//!
//! All endpoints use only the in-process synthetic fixtures.

use std::time::Duration;

use axum::{
    extract::{Path, Query},
    http::{header, StatusCode},
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use serde::Deserialize;
use serde_json::json;

use crate::cancel::CancellationToken;
use crate::exec::{self, QueryPlan, QueryRequest, RunStats};
use crate::validate::Scenario;

/// Build the application router.
pub fn app() -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/query", post(run_query))
        .route("/validate/:scenario", post(run_scenario))
        .route("/query/stream", get(stream_query))
}

async fn health() -> Json<serde_json::Value> {
    Json(json!({ "status": "ok", "service": "pull-query" }))
}

fn error_dto(e: &crate::QueryError) -> serde_json::Value {
    json!({ "kind": e.kind().as_str(), "message": e.message() })
}

fn status_for(kind: Option<crate::ErrorKind>) -> StatusCode {
    match kind {
        None => StatusCode::OK,
        Some(crate::ErrorKind::InvalidInput) => StatusCode::BAD_REQUEST,
        Some(crate::ErrorKind::StateConflict) => StatusCode::CONFLICT,
        Some(crate::ErrorKind::ResourceExhausted) => StatusCode::INSUFFICIENT_STORAGE,
        Some(crate::ErrorKind::Timeout) => StatusCode::REQUEST_TIMEOUT,
        Some(crate::ErrorKind::Cancelled) => StatusCode::CONFLICT,
        Some(crate::ErrorKind::ComputationFailed) => StatusCode::INTERNAL_SERVER_ERROR,
    }
}

fn empty_stats() -> RunStats {
    RunStats {
        rows_out: 0,
        batches_out: 0,
        runs_spilled: 0,
        spilled_bytes: 0,
        peak_open_files: 0,
        open_files_after_close: 0,
        buffered_bytes_after_close: 0,
        elapsed_ms: 0,
        terminal: String::new(),
        error_kind: None,
    }
}

async fn run_query(Json(req): Json<QueryRequest>) -> impl IntoResponse {
    let token = CancellationToken::new();
    let result = tokio::task::spawn_blocking(move || exec::execute(&req, token))
        .await
        .expect("query task panicked");

    match result {
        Ok(out) => {
            let rows = exec::rows_to_json(&out.batches).unwrap_or_default();
            let status = status_for(out.error.as_ref().map(|e| e.kind()));
            let body = json!({
                "run_id": out.run_id,
                "ok": out.error.is_none(),
                "plan": out.plan,
                "stats": out.stats,
                "rows": rows,
                "error": out.error.as_ref().map(error_dto),
                "log": out.log,
            });
            (status, Json(body))
        }
        Err(build_error) => (
            StatusCode::BAD_REQUEST,
            Json(json!({
                "run_id": "none",
                "ok": false,
                "plan": QueryPlan { stages: vec![] },
                "stats": empty_stats(),
                "rows": [],
                "error": error_dto(&build_error),
                "log": format!("plan build failed: {build_error}"),
            })),
        ),
    }
}

async fn run_scenario(Path(name): Path<String>) -> impl IntoResponse {
    let Some(scenario) = scenario_by_name(&name) else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({
                "error": "unknown scenario",
                "available": ["scan", "spill_sort", "join_sort", "timeout", "cancel"]
            })),
        );
    };
    let out = tokio::task::spawn_blocking(move || scenario.run())
        .await
        .expect("scenario task panicked");
    let rows = exec::rows_to_json(&out.batches).unwrap_or_default();
    let status = status_for(out.error.as_ref().map(|e| e.kind()));
    let body = json!({
        "run_id": out.run_id,
        "scenario": name,
        "ok": out.error.is_none(),
        "rows": rows,
        "error": out.error.as_ref().map(error_dto),
        "resources": {
            "open_files_after_close": out.tracker.open_files(),
            "buffered_bytes_after_close": out.tracker.buffered_bytes(),
            "spilled_bytes": out.tracker.spilled_bytes(),
            "runs_spilled": out.tracker.spill_files_created(),
            "peak_open_files": out.tracker.peak_open_files(),
        },
        "log": out.diag.render(),
    });
    (status, Json(body))
}

fn scenario_by_name(name: &str) -> Option<Scenario> {
    match name {
        "scan" => Some(Scenario::ScanUsers),
        "spill_sort" => Some(Scenario::SpillSort {
            rows: 60,
            batch_size: 5,
            budget: 300,
        }),
        "join_sort" => Some(Scenario::JoinSort {
            order_rows: 40,
            batch_size: 5,
            budget: 600,
        }),
        "timeout" => Some(Scenario::Timeout {
            per_batch_delay: Duration::from_millis(100),
            deadline: Duration::from_millis(30),
        }),
        "cancel" => Some(Scenario::CancelRace {
            per_batch_delay: Duration::from_millis(20),
            batches: 10,
            cancel_at: Duration::from_millis(50),
        }),
        _ => None,
    }
}

#[derive(Deserialize)]
struct StreamParams {
    #[serde(default = "default_batches")]
    batches: usize,
    #[serde(default = "default_delay_ms")]
    delay_ms: u64,
    cancel_after_ms: Option<u64>,
}
fn default_batches() -> usize {
    20
}
fn default_delay_ms() -> u64 {
    20
}

/// Drive a blocking source on a worker thread and return newline-delimited
/// JSON. A separate task flips the cancel token after `cancel_after_ms`,
/// demonstrating user cancellation distinct from timeout (no deadline is set on
/// this endpoint). Already-emitted rows are returned in every case.
async fn stream_query(Query(p): Query<StreamParams>) -> impl IntoResponse {
    let token = CancellationToken::new();
    if let Some(ms) = p.cancel_after_ms {
        let t = token.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(ms)).await;
            t.cancel();
        });
    }

    let num_batches = p.batches;
    let delay = Duration::from_millis(p.delay_ms);
    let out = tokio::task::spawn_blocking(move || {
        crate::validate::run_blocking_scan(delay, num_batches, token)
    })
    .await
    .expect("stream task panicked");

    let rows = exec::rows_to_json(&out.batches).unwrap_or_default();
    let mut nd = String::new();
    for (i, row) in rows.iter().enumerate() {
        nd.push_str(&json!({ "event": "row", "seq": i, "data": row }).to_string());
        nd.push('\n');
    }
    nd.push_str(
        &json!({
            "event": "stats",
            "run_id": out.run_id,
            "ok": out.error.is_none(),
            "rows_emitted": rows.len(),
            "error": out.error.as_ref().map(error_dto),
            "resources": {
                "open_files_after_close": out.tracker.open_files(),
                "buffered_bytes_after_close": out.tracker.buffered_bytes(),
            },
        })
        .to_string(),
    );
    nd.push('\n');
    let status = status_for(out.error.as_ref().map(|e| e.kind()));
    (
        status,
        [(header::CONTENT_TYPE, "application/x-ndjson".to_string())],
        nd,
    )
}
