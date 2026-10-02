//! Axum HTTP boundary.
//!
//! Every request gets a fresh run id (uuid v4) that is logged with the
//! request and echoed in error bodies, so any failure can be replayed
//! from the server log. Handlers translate between HTTP/JSON and engine
//! calls; all semantics live in the engine.

use std::sync::Arc;

use axum::{
    Json, Router,
    extract::{Path, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
};
use base64::{Engine as _, engine::general_purpose::STANDARD as B64};
use serde::{Deserialize, Serialize};
use tokio::sync::Mutex;
use uuid::Uuid;

use crate::{
    diag::{StatsReport, VerifyReport},
    engine::{CowEngine, SnapId, WriteReq},
    error::AppError,
};

pub struct AppState {
    pub engine: Mutex<CowEngine>,
    /// Run id of this service process; request run ids are per-request.
    pub service_run_id: String,
}

pub fn router(state: Arc<AppState>) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/snapshots", post(create_snapshot).get(list_snapshots))
        .route("/snapshots/{id}", axum::routing::delete(delete_snapshot))
        .route("/snapshots/{id}/writes", post(write_batch))
        .route("/snapshots/{id}/pages/{page}", get(read_page))
        .route("/diag/stats", get(diag_stats))
        .route("/diag/verify", get(diag_verify))
        .with_state(state)
}

fn run_id() -> String {
    Uuid::new_v4().to_string()
}

#[derive(Serialize)]
struct OkBody<T: Serialize> {
    run_id: String,
    #[serde(flatten)]
    body: T,
}

fn ok<T: Serialize>(run_id: String, body: T) -> Response {
    (StatusCode::OK, Json(OkBody { run_id, body })).into_response()
}

async fn health(State(st): State<Arc<AppState>>) -> impl IntoResponse {
    Json(serde_json::json!({
        "status": "ok",
        "service_run_id": st.service_run_id,
    }))
}

#[derive(Deserialize)]
struct CreateReq {
    parent: Option<SnapId>,
}

#[derive(Serialize)]
struct CreateResp {
    snapshot_id: SnapId,
}

async fn create_snapshot(
    State(st): State<Arc<AppState>>,
    body: Option<Json<CreateReq>>,
) -> Response {
    let rid = run_id();
    let parent = body.and_then(|Json(b)| b.parent);
    tracing::info!(run_id = %rid, ?parent, "create_snapshot");
    let mut engine = st.engine.lock().await;
    match engine.create_snapshot(parent) {
        Ok(id) => (
            StatusCode::CREATED,
            Json(OkBody { run_id: rid, body: CreateResp { snapshot_id: id } }),
        )
            .into_response(),
        Err(e) => {
            tracing::warn!(run_id = %rid, error = %e, "create_snapshot failed");
            e.into_response_with(&rid)
        }
    }
}

async fn list_snapshots(State(st): State<Arc<AppState>>) -> Response {
    let rid = run_id();
    let engine = st.engine.lock().await;
    let stats = engine.stats();
    ok(rid, serde_json::json!({ "snapshots": stats.snapshots }))
}

async fn delete_snapshot(
    State(st): State<Arc<AppState>>,
    Path(id): Path<SnapId>,
) -> Response {
    let rid = run_id();
    tracing::info!(run_id = %rid, snapshot = id, "delete_snapshot");
    let mut engine = st.engine.lock().await;
    match engine.delete_snapshot(id) {
        Ok(()) => ok(rid, serde_json::json!({ "deleted": id })),
        Err(e) => {
            tracing::warn!(run_id = %rid, error = %e, "delete_snapshot failed");
            e.into_response_with(&rid)
        }
    }
}

#[derive(Deserialize)]
struct WriteReqJson {
    page: usize,
    offset: usize,
    /// Base64-encoded bytes.
    data_b64: String,
}

#[derive(Deserialize)]
struct WritesBody {
    writes: Vec<WriteReqJson>,
}

#[derive(Serialize)]
struct WriteResp {
    pages_written: usize,
    cow_copies: u64,
    in_place_writes: u64,
    fresh_allocs: u64,
}

async fn write_batch(
    State(st): State<Arc<AppState>>,
    Path(id): Path<SnapId>,
    Json(body): Json<WritesBody>,
) -> Response {
    let rid = run_id();
    let mut writes = Vec::with_capacity(body.writes.len());
    for w in &body.writes {
        match B64.decode(&w.data_b64) {
            Ok(data) => writes.push(WriteReq { page: w.page, offset: w.offset, data }),
            Err(e) => {
                return AppError::Input(format!("invalid base64 in write: {e}"))
                    .into_response_with(&rid);
            }
        }
    }
    tracing::info!(run_id = %rid, snapshot = id, batch = writes.len(), "write_batch");
    let mut engine = st.engine.lock().await;
    match engine.write_batch(id, &writes) {
        Ok(rep) => ok(
            rid,
            WriteResp {
                pages_written: rep.pages_written,
                cow_copies: rep.cow_copies,
                in_place_writes: rep.in_place_writes,
                fresh_allocs: rep.fresh_allocs,
            },
        ),
        Err(e) => {
            tracing::warn!(run_id = %rid, error = %e, category = ?e.category(), "write_batch failed");
            e.into_response_with(&rid)
        }
    }
}

#[derive(Serialize)]
struct PageResp {
    snapshot: SnapId,
    page: usize,
    data_b64: String,
}

async fn read_page(
    State(st): State<Arc<AppState>>,
    Path((id, page)): Path<(SnapId, usize)>,
) -> Response {
    let rid = run_id();
    let engine = st.engine.lock().await;
    match engine.read_page(id, page) {
        Ok(data) => ok(rid, PageResp { snapshot: id, page, data_b64: B64.encode(data) }),
        Err(e) => e.into_response_with(&rid),
    }
}

async fn diag_stats(State(st): State<Arc<AppState>>) -> Response {
    let rid = run_id();
    let engine = st.engine.lock().await;
    let stats: StatsReport = engine.stats();
    ok(rid, stats)
}

async fn diag_verify(State(st): State<Arc<AppState>>) -> Response {
    let rid = run_id();
    let engine = st.engine.lock().await;
    let report: VerifyReport = engine.verify();
    ok(rid, report)
}
