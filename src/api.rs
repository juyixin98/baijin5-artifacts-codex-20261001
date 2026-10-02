//! Axum HTTP layer. Thin translation between JSON/base64 DTOs and the
//! engine; all semantics live in `engine.rs`.
//!
//! Error contract: every failure is returned as
//! `{ "error": { "category", "code", "message" } }` with the status code
//! derived from the category (see `error.rs`).

use std::sync::{Arc, Mutex};

use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use base64::Engine as _;
use serde::{Deserialize, Serialize};

use crate::engine::{Engine, PageWrite};
use crate::error::{ErrorCategory, ServiceError};

pub type SharedEngine = Arc<Mutex<Engine>>;

pub fn router(engine: SharedEngine) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/live/writes", post(post_writes))
        .route("/live/pages/{page}", get(get_live_page))
        .route("/snapshots", post(post_snapshot).get(list_snapshots))
        .route("/snapshots/{id}", get(get_snapshot).delete(delete_snapshot))
        .route("/snapshots/{id}/pages/{page}", get(get_snapshot_page))
        .route("/diag/stats", get(diag_stats))
        .route("/diag/audit", post(diag_audit))
        .route("/diag/events", get(diag_events))
        .route("/diag/quarantine/clear", post(diag_clear_quarantine))
        .with_state(engine)
}

fn lock(st: &SharedEngine) -> std::sync::MutexGuard<'_, Engine> {
    // A poisoned mutex means a panic occurred mid-mutation; surface it as an
    // internal error rather than silently recovering.
    st.lock().unwrap_or_else(|p| p.into_inner())
}

// ---------------------------------------------------------------------
// DTOs
// ---------------------------------------------------------------------

#[derive(Deserialize)]
struct WriteItem {
    page: u32,
    offset: u32,
    /// Base64-encoded bytes.
    data_b64: String,
}

#[derive(Deserialize)]
struct WriteRequest {
    writes: Vec<WriteItem>,
}

#[derive(Deserialize)]
struct ForkRequest {
    name: Option<String>,
}

#[derive(Deserialize)]
struct AuditRequest {
    enforce: Option<bool>,
}

#[derive(Deserialize)]
struct EventsQuery {
    limit: Option<usize>,
}

#[derive(Serialize)]
struct PageResponse {
    page: u32,
    data_b64: String,
}

#[derive(Serialize)]
struct ErrorBody {
    error: ErrorDetail,
}

#[derive(Serialize)]
struct ErrorDetail {
    category: ErrorCategory,
    code: &'static str,
    message: String,
}

impl IntoResponse for ServiceError {
    fn into_response(self) -> Response {
        let status = match self.category {
            ErrorCategory::Input => StatusCode::BAD_REQUEST,
            ErrorCategory::StateConflict => StatusCode::CONFLICT,
            ErrorCategory::ResourceExhausted => StatusCode::INSUFFICIENT_STORAGE,
            ErrorCategory::Integrity => StatusCode::INTERNAL_SERVER_ERROR,
            ErrorCategory::Internal => StatusCode::INTERNAL_SERVER_ERROR,
        };
        let body = ErrorBody {
            error: ErrorDetail {
                category: self.category,
                code: self.code,
                message: self.message,
            },
        };
        (status, Json(body)).into_response()
    }
}

type ApiResult<T> = Result<T, ServiceError>;

// ---------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------

async fn health(State(st): State<SharedEngine>) -> Json<serde_json::Value> {
    let run_id = lock(&st).run_id().to_string();
    Json(serde_json::json!({ "status": "ok", "run_id": run_id }))
}

async fn post_writes(
    State(st): State<SharedEngine>,
    Json(req): Json<WriteRequest>,
) -> ApiResult<Json<crate::engine::BatchReport>> {
    let mut writes = Vec::with_capacity(req.writes.len());
    for (i, w) in req.writes.into_iter().enumerate() {
        let data = base64::engine::general_purpose::STANDARD
            .decode(&w.data_b64)
            .map_err(|e| ServiceError::input("bad_base64", format!("writes[{i}].data_b64: {e}")))?;
        writes.push(PageWrite {
            page: w.page,
            offset: w.offset,
            data,
        });
    }
    let report = lock(&st).batch_write(writes)?;
    Ok(Json(report))
}

async fn get_live_page(
    State(st): State<SharedEngine>,
    Path(page): Path<u32>,
) -> ApiResult<Json<PageResponse>> {
    let data = lock(&st).read_live_page(page)?;
    Ok(Json(PageResponse {
        page,
        data_b64: base64::engine::general_purpose::STANDARD.encode(data),
    }))
}

async fn post_snapshot(
    State(st): State<SharedEngine>,
    Json(req): Json<ForkRequest>,
) -> ApiResult<Json<crate::engine::SnapshotInfo>> {
    let info = lock(&st).fork(req.name)?;
    Ok(Json(info))
}

async fn list_snapshots(State(st): State<SharedEngine>) -> Json<Vec<crate::engine::SnapshotInfo>> {
    Json(lock(&st).list_snapshots())
}

async fn get_snapshot(
    State(st): State<SharedEngine>,
    Path(id): Path<u64>,
) -> ApiResult<Json<crate::engine::SnapshotInfo>> {
    lock(&st).snapshot_info(id).map(Json).ok_or_else(|| {
        ServiceError::conflict(
            "snapshot_not_found",
            format!("snapshot {id} does not exist"),
        )
    })
}

async fn delete_snapshot(
    State(st): State<SharedEngine>,
    Path(id): Path<u64>,
) -> ApiResult<Json<crate::engine::SnapshotInfo>> {
    let info = lock(&st).delete_snapshot(id)?;
    Ok(Json(info))
}

async fn get_snapshot_page(
    State(st): State<SharedEngine>,
    Path((id, page)): Path<(u64, u32)>,
) -> ApiResult<Json<PageResponse>> {
    let data = lock(&st).read_snapshot_page(id, page)?;
    Ok(Json(PageResponse {
        page,
        data_b64: base64::engine::general_purpose::STANDARD.encode(data),
    }))
}

async fn diag_stats(State(st): State<SharedEngine>) -> Json<crate::engine::StatsView> {
    Json(lock(&st).stats())
}

async fn diag_audit(
    State(st): State<SharedEngine>,
    Json(req): Json<AuditRequest>,
) -> ApiResult<Json<crate::engine::AuditReport>> {
    let report = lock(&st).audit(req.enforce.unwrap_or(false))?;
    Ok(Json(report))
}

async fn diag_events(
    State(st): State<SharedEngine>,
    Query(q): Query<EventsQuery>,
) -> Json<Vec<crate::eventlog::Event>> {
    Json(lock(&st).events_recent(q.limit.unwrap_or(50).min(256)))
}

async fn diag_clear_quarantine(State(st): State<SharedEngine>) -> ApiResult<StatusCode> {
    lock(&st).clear_quarantine()?;
    Ok(StatusCode::NO_CONTENT)
}
