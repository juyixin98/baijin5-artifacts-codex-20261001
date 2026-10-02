//! Axum HTTP interface. Handlers only translate HTTP <-> engine calls; all
//! semantics live in the engine. A background pump (server mode) or manual
//! `tick_at` (tests) drives the model.

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::Json;
use axum::routing::{get, post};
use axum::Router;
use serde::{Deserialize, Serialize};

use crate::adapter::IoAdapter;
use crate::diag::DiagEvent;
use crate::engine::{CancelAccept, CancelError, Engine, Stats, SubmitError};
use crate::handle::HandleError;
use crate::model::{ConnHandle, ConnId, Generation, IoOp, RecordView, SubmissionId, UserData};

/// Engine + adapter + wall clock, shared by all handlers.
pub struct Runtime {
    pub engine: Engine,
    pub adapter: Box<dyn IoAdapter>,
    started: Instant,
}

impl Runtime {
    pub fn new(engine: Engine, adapter: Box<dyn IoAdapter>) -> Self {
        Self {
            engine,
            adapter,
            started: Instant::now(),
        }
    }

    /// Drive the model with the real clock (server mode).
    pub fn tick(&mut self) {
        let now = self.started.elapsed().as_millis() as u64;
        self.tick_at(now);
    }

    /// Drive the model with an explicit clock (deterministic tests).
    pub fn tick_at(&mut self, now_ms: u64) {
        self.engine.advance_time(now_ms);
        self.engine.pump(self.adapter.as_mut());
    }
}

pub type Shared = Arc<Mutex<Runtime>>;

pub fn build_router(shared: Shared) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/stats", get(stats))
        .route("/connections", post(open_connection))
        .route("/connections/{conn}", get(get_connection))
        .route("/connections/{conn}/close", post(close_connection))
        .route("/connections/{conn}/submissions", post(submit))
        .route("/submissions/{sid}", get(get_submission))
        .route("/submissions/{sid}/cancel", post(cancel))
        .route("/diag/events", get(diag_events))
        .with_state(shared)
}

/// Background pump for server mode.
pub fn spawn_pump(shared: Shared, interval_ms: u64) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(Duration::from_millis(interval_ms.max(1)));
        loop {
            interval.tick().await;
            if let Ok(mut rt) = shared.lock() {
                rt.tick();
            }
        }
    })
}

// ---- DTOs ---------------------------------------------------------------

#[derive(Debug, Deserialize)]
pub struct OpenReq {
    request_id: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct ConnHandleDto {
    conn_id: u64,
    generation: u64,
}

#[derive(Debug, Deserialize)]
pub struct SubmitReq {
    generation: u64,
    op: IoOp,
    request_id: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct SubmitResp {
    user_data: UserData,
    note: &'static str,
}

#[derive(Debug, Deserialize)]
pub struct CancelReq {
    conn: u64,
    generation: u64,
    request_id: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct CancelResp {
    accepted: &'static str,
    note: &'static str,
}

#[derive(Debug, Deserialize)]
pub struct CloseReq {
    generation: u64,
    request_id: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct SinceQuery {
    since: Option<u64>,
}

#[derive(Debug, Deserialize)]
pub struct GenQuery {
    generation: Option<u64>,
}

#[derive(Debug, Serialize)]
pub struct ErrorBody {
    error: ErrorDetail,
}

#[derive(Debug, Serialize)]
struct ErrorDetail {
    code: &'static str,
    reason: String,
}

type ApiResult<T> = Result<T, (StatusCode, Json<ErrorBody>)>;

fn err(status: StatusCode, code: &'static str, reason: String) -> (StatusCode, Json<ErrorBody>) {
    (
        status,
        Json(ErrorBody {
            error: ErrorDetail { code, reason },
        }),
    )
}

fn handle_err(e: &HandleError) -> (StatusCode, Json<ErrorBody>) {
    match e {
        HandleError::UnknownConnection => {
            err(StatusCode::NOT_FOUND, "unknown_connection", e.to_string())
        }
        HandleError::StaleGeneration { .. } => {
            err(StatusCode::CONFLICT, "stale_generation", e.to_string())
        }
        HandleError::NotOpen { .. } => err(StatusCode::CONFLICT, "not_open", e.to_string()),
        HandleError::NotOwner => err(StatusCode::CONFLICT, "not_owner", e.to_string()),
    }
}

// ---- handlers -------------------------------------------------------------

async fn health() -> &'static str {
    "ok"
}

async fn stats(State(rt): State<Shared>) -> Json<Stats> {
    Json(rt.lock().expect("runtime lock").engine.stats())
}

async fn open_connection(
    State(rt): State<Shared>,
    body: Option<Json<OpenReq>>,
) -> Json<ConnHandleDto> {
    let request_id = body.and_then(|b| b.request_id.clone());
    let handle = rt
        .lock()
        .expect("runtime lock")
        .engine
        .open_connection(request_id);
    Json(ConnHandleDto {
        conn_id: handle.conn.0,
        generation: handle.generation.0,
    })
}

async fn get_connection(
    State(rt): State<Shared>,
    Path(conn): Path<u64>,
    Query(q): Query<GenQuery>,
) -> ApiResult<Json<serde_json::Value>> {
    let rt = rt.lock().expect("runtime lock");
    let view = rt
        .engine
        .connection_view(ConnId(conn))
        .ok_or_else(|| err(StatusCode::NOT_FOUND, "unknown_connection", format!("connection {conn} does not exist")))?;
    if let Some(gen) = q.generation {
        if gen != view.generation.0 {
            return Err(err(
                StatusCode::CONFLICT,
                "stale_generation",
                format!("queried generation {gen}, slot is at {}", view.generation.0),
            ));
        }
    }
    Ok(Json(serde_json::to_value(view).expect("ConnView serializes")))
}

async fn close_connection(
    State(rt): State<Shared>,
    Path(conn): Path<u64>,
    Json(req): Json<CloseReq>,
) -> ApiResult<(StatusCode, Json<serde_json::Value>)> {
    let mut rt = rt.lock().expect("runtime lock");
    let handle = ConnHandle {
        conn: ConnId(conn),
        generation: Generation(req.generation),
    };
    let view = rt
        .engine
        .close_connection(handle, req.request_id)
        .map_err(|e| handle_err(&e))?;
    let status = match view.lifecycle {
        crate::handle::ConnLifecycle::Closed => StatusCode::OK,
        _ => StatusCode::ACCEPTED,
    };
    Ok((
        status,
        Json(serde_json::to_value(view).expect("ConnView serializes")),
    ))
}

async fn submit(
    State(rt): State<Shared>,
    Path(conn): Path<u64>,
    Json(req): Json<SubmitReq>,
) -> ApiResult<(StatusCode, Json<SubmitResp>)> {
    let mut rt = rt.lock().expect("runtime lock");
    let handle = ConnHandle {
        conn: ConnId(conn),
        generation: Generation(req.generation),
    };
    match rt.engine.submit(handle, req.op, req.request_id) {
        Ok(token) => Ok((
            StatusCode::CREATED,
            Json(SubmitResp {
                user_data: token,
                note: "queued; dispatch is asynchronous — poll GET /submissions/{id}",
            }),
        )),
        Err(SubmitError::QueueFull { capacity }) => Err(err(
            StatusCode::TOO_MANY_REQUESTS,
            "queue_full",
            format!("submission queue full (capacity {capacity}); explicit backpressure — retry later"),
        )),
        Err(SubmitError::StaleHandle(e)) => Err(handle_err(&e)),
    }
}

async fn get_submission(
    State(rt): State<Shared>,
    Path(sid): Path<u64>,
) -> ApiResult<Json<RecordView>> {
    let rt = rt.lock().expect("runtime lock");
    rt.engine
        .record_view(SubmissionId(sid))
        .map(Json)
        .ok_or_else(|| err(StatusCode::NOT_FOUND, "unknown_submission", format!("submission {sid} does not exist")))
}

async fn cancel(
    State(rt): State<Shared>,
    Path(sid): Path<u64>,
    Json(req): Json<CancelReq>,
) -> ApiResult<(StatusCode, Json<CancelResp>)> {
    let mut rt = rt.lock().expect("runtime lock");
    let handle = ConnHandle {
        conn: ConnId(req.conn),
        generation: Generation(req.generation),
    };
    match rt.engine.cancel(handle, SubmissionId(sid), req.request_id) {
        Ok(CancelAccept::Immediate) => Ok((
            StatusCode::ACCEPTED,
            Json(CancelResp {
                accepted: "immediate",
                note: "cancelled before dispatch; the IO never reached the device",
            }),
        )),
        Ok(CancelAccept::Forwarded) => Ok((
            StatusCode::ACCEPTED,
            Json(CancelResp {
                accepted: "forwarded",
                note: "cancel request accepted; the IO may still complete — \
                       the final completion record is authoritative",
            }),
        )),
        Err(CancelError::UnknownSubmission) => Err(err(
            StatusCode::NOT_FOUND,
            "unknown_submission",
            format!("submission {sid} does not exist"),
        )),
        Err(CancelError::AlreadyFinal(outcome)) => Err(err(
            StatusCode::CONFLICT,
            "already_final",
            format!("submission {sid} is already final: {outcome:?}"),
        )),
        Err(CancelError::StaleHandle(e)) => Err(handle_err(&e)),
    }
}

async fn diag_events(
    State(rt): State<Shared>,
    Query(q): Query<SinceQuery>,
) -> Json<serde_json::Value> {
    let rt = rt.lock().expect("runtime lock");
    let since = q.since.unwrap_or(0);
    let events: Vec<&DiagEvent> = rt
        .engine
        .events()
        .iter()
        .filter(|e| e.seq > since)
        .collect();
    Json(serde_json::json!({ "events": events }))
}
