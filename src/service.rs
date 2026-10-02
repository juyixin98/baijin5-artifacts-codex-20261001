//! Axum service entry: run plans, cancel running queries, inspect resources.
//!
//! Endpoints:
//! - `POST /query`                — validate + execute a plan, return rows
//! - `POST /query/{run_id}/cancel`— user-cancel a running query
//! - `GET  /metrics`              — live queries + resource snapshots
//! - `GET  /health`               — liveness
//!
//! Error semantics (see README): the HTTP status is derived from the error
//! *category* — 400 input, 408 timeout, 409 state conflict, 499 user cancel,
//! 507 resource exhausted, 500 compute. The body always carries the
//! machine-readable `category` and the `run_id` for log correlation.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::{Deserialize, Serialize};
use tracing::{info, warn};

use crate::batch::TypedBatch;
use crate::cancel::CancelToken;
use crate::error::{CancelKind, ErrorCategory, QueryError};
use crate::exec::{ExecutionContext, RunId};
use crate::plan::{build_plan, Plan};
use crate::resources::{ResourceRegistry, ResourceSnapshot};

/// Max rows embedded in an HTTP response body (row_count is always exact).
const MAX_RESPONSE_ROWS: usize = 500;

pub struct LiveQuery {
    pub token: CancelToken,
    pub resources: Arc<ResourceRegistry>,
}

pub struct AppState {
    pub memory_limit_bytes: usize,
    pub spill_root: PathBuf,
    pub live: Mutex<HashMap<String, LiveQuery>>,
    pub queries_started: AtomicU64,
    pub queries_finished: AtomicU64,
}

impl AppState {
    pub fn new(memory_limit_bytes: usize, spill_root: PathBuf) -> Self {
        std::fs::create_dir_all(&spill_root).expect("create spill root");
        Self {
            memory_limit_bytes,
            spill_root,
            live: Mutex::new(HashMap::new()),
            queries_started: AtomicU64::new(0),
            queries_finished: AtomicU64::new(0),
        }
    }
}

pub fn router(state: Arc<AppState>) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/query", post(run_query))
        .route("/query/{run_id}/cancel", post(cancel_query))
        .route("/metrics", get(metrics))
        .with_state(state)
}

async fn health() -> &'static str {
    "ok"
}

#[derive(Debug, Deserialize)]
pub struct QueryRequest {
    pub plan: Plan,
    pub timeout_ms: Option<u64>,
    pub max_rows: Option<usize>,
    pub memory_limit_bytes: Option<usize>,
}

#[derive(Debug, Serialize)]
pub struct QueryResponse {
    pub run_id: RunId,
    pub status: &'static str,
    pub columns: Vec<String>,
    pub row_count: usize,
    pub rows: Vec<Vec<serde_json::Value>>,
    pub truncated: bool,
    pub elapsed_ms: u128,
    pub metrics: ResourceSnapshot,
}

#[derive(Debug, Serialize)]
pub struct ErrorResponse {
    pub run_id: Option<String>,
    pub category: ErrorCategory,
    pub message: String,
}

fn status_for(category: ErrorCategory) -> StatusCode {
    match category {
        ErrorCategory::Input => StatusCode::BAD_REQUEST,
        ErrorCategory::StateConflict => StatusCode::CONFLICT,
        ErrorCategory::ResourceExhausted => StatusCode::INSUFFICIENT_STORAGE,
        ErrorCategory::Compute => StatusCode::INTERNAL_SERVER_ERROR,
        ErrorCategory::CancelledUser => StatusCode::from_u16(499).expect("valid status"),
        ErrorCategory::CancelledTimeout => StatusCode::REQUEST_TIMEOUT,
    }
}

fn error_response(run_id: &RunId, err: &QueryError) -> (StatusCode, Json<ErrorResponse>) {
    (
        status_for(err.category()),
        Json(ErrorResponse {
            run_id: Some(run_id.to_string()),
            category: err.category(),
            message: err.to_string(),
        }),
    )
}

async fn run_query(
    State(state): State<Arc<AppState>>,
    Json(request): Json<QueryRequest>,
) -> Result<Json<QueryResponse>, (StatusCode, Json<ErrorResponse>)> {
    let memory_limit = request.memory_limit_bytes.unwrap_or(state.memory_limit_bytes);
    let run_id = RunId::new();
    let resources = ResourceRegistry::new(memory_limit, state.spill_root.join(&run_id.0));
    let timeout = request.timeout_ms.map(Duration::from_millis);
    let ctx = ExecutionContext::with_run_id(run_id, resources, timeout);
    info!(run_id = %ctx.run_id, "query accepted");

    state.queries_started.fetch_add(1, Ordering::AcqRel);
    state.live.lock().expect("live mutex").insert(
        ctx.run_id.to_string(),
        LiveQuery { token: ctx.cancel.clone(), resources: Arc::clone(&ctx.resources) },
    );

    let result = execute(&ctx, &request).await;

    // Deterministic teardown: shut the context down (aborts and awaits the
    // deadline watcher), deregister, then report the final snapshot.
    ctx.shutdown().await;
    // remove_dir only succeeds when empty: a leftover spill file would keep
    // the run directory (and the leak) visible on disk.
    let _ = std::fs::remove_dir(ctx.resources.spill_dir());
    state.live.lock().expect("live mutex").remove(&ctx.run_id.to_string());
    state.queries_finished.fetch_add(1, Ordering::AcqRel);

    match result {
        Ok(mut response) => {
            response.run_id = ctx.run_id.clone();
            response.metrics = ctx.resources.snapshot();
            info!(run_id = %ctx.run_id, rows = response.row_count, "query finished");
            Ok(Json(response))
        }
        Err(err) => {
            warn!(run_id = %ctx.run_id, category = ?err.category(), error = %err, "query failed");
            Err(error_response(&ctx.run_id, &err))
        }
    }
}

async fn execute(
    ctx: &Arc<ExecutionContext>,
    request: &QueryRequest,
) -> Result<QueryResponse, QueryError> {
    let mut built = build_plan(&request.plan, ctx)?; // validation entry
    let columns: Vec<String> = built.schema.fields.iter().map(|f| f.name.clone()).collect();
    let max_rows = request.max_rows.unwrap_or(usize::MAX);
    let mut batches: Vec<TypedBatch> = Vec::new();
    let mut row_count = 0usize;

    let collect_result: Result<(), QueryError> = async {
        while let Some(batch) = built.root.next_batch().await? {
            row_count += batch.num_rows();
            if row_count > max_rows {
                return Err(QueryError::resource(format!(
                    "row limit exceeded: more than {max_rows} rows produced"
                )));
            }
            batches.push(batch);
        }
        Ok(())
    }
    .await;

    // Always close the tree, even on error; a close failure only wins if
    // there was no prior error.
    let close_result = built.root.close().await;
    collect_result.and(close_result)?;

    let (rows, truncated) = rows_to_json(&batches, MAX_RESPONSE_ROWS);
    Ok(QueryResponse {
        run_id: ctx.run_id.clone(),
        status: "ok",
        columns,
        row_count,
        rows,
        truncated,
        elapsed_ms: ctx.started_at.elapsed().as_millis(),
        metrics: ctx.resources.snapshot(),
    })
}

async fn cancel_query(
    State(state): State<Arc<AppState>>,
    Path(run_id): Path<String>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    let token = {
        let live = state.live.lock().expect("live mutex");
        live.get(&run_id).map(|q| q.token.clone())
    };
    match token {
        Some(token) => {
            token.cancel(CancelKind::User);
            Ok(Json(serde_json::json!({ "run_id": run_id, "cancelled": true })))
        }
        None => Err(StatusCode::NOT_FOUND),
    }
}

#[derive(Debug, Serialize)]
struct LiveQueryMetrics {
    run_id: String,
    resources: ResourceSnapshot,
}

async fn metrics(State(state): State<Arc<AppState>>) -> Json<serde_json::Value> {
    let live: Vec<LiveQueryMetrics> = state
        .live
        .lock()
        .expect("live mutex")
        .iter()
        .map(|(run_id, q)| LiveQueryMetrics {
            run_id: run_id.clone(),
            resources: q.resources.snapshot(),
        })
        .collect();
    Json(serde_json::json!({
        "queries_started": state.queries_started.load(Ordering::Acquire),
        "queries_finished": state.queries_finished.load(Ordering::Acquire),
        "active_queries": live.len(),
        "live": live,
    }))
}

/// Convert batches to JSON rows, capped at `cap` rows. Returns (rows, truncated).
fn rows_to_json(
    batches: &[TypedBatch],
    cap: usize,
) -> (Vec<Vec<serde_json::Value>>, bool) {
    let mut rows = Vec::new();
    let mut truncated = false;
    'outer: for batch in batches {
        for row in 0..batch.num_rows() {
            if rows.len() >= cap {
                truncated = true;
                break 'outer;
            }
            rows.push(
                batch
                    .chunk()
                    .arrays()
                    .iter()
                    .map(|array| value_to_json(array.as_ref(), row))
                    .collect(),
            );
        }
    }
    (rows, truncated)
}

fn value_to_json(array: &dyn arrow2::array::Array, row: usize) -> serde_json::Value {
    use arrow2::array::{Int64Array, Utf8Array};
    use arrow2::datatypes::DataType;
    if array.is_null(row) {
        return serde_json::Value::Null;
    }
    match array.data_type() {
        DataType::Int64 => {
            let values = array.as_any().downcast_ref::<Int64Array>().expect("typed");
            serde_json::json!(values.value(row))
        }
        DataType::Utf8 => {
            let values = array.as_any().downcast_ref::<Utf8Array<i32>>().expect("typed");
            serde_json::json!(values.value(row))
        }
        other => serde_json::json!(format!("<unsupported {other:?}>")),
    }
}
