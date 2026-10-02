//! Diagnostic HTTP interface (Axum).
//!
//! Every request carries a request id (client-supplied `x-request-id` or a
//! generated UUID) that is echoed in the response header and attached to log
//! lines. Logs are redacted: only sequence numbers, counts and categories are
//! logged — never per-process stat payloads or filesystem paths.

use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use axum::extract::{Query, Request, State};
use axum::http::{HeaderValue, StatusCode};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Extension, Json, Router};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::engine::{Engine, IngestOutcome};
use crate::model::{CpuDelta, Pid, Snapshot, TreeView, UndeterminedInterval};
use crate::snapshot;
use crate::store::Store;

pub struct AppState {
    pub engine: Mutex<Engine>,
    pub store: Store,
}

pub type Shared = Arc<AppState>;

#[derive(Clone)]
struct RequestId(String);

pub fn router(state: Shared) -> Router {
    Router::new()
        .route("/healthz", get(healthz))
        .route("/v1/snapshots", post(ingest_snapshot))
        .route("/v1/deltas", get(list_deltas))
        .route("/v1/tree", get(get_tree))
        .route("/v1/intervals/undetermined", get(list_undetermined))
        .layer(middleware::from_fn(request_id_middleware))
        .with_state(state)
}

async fn request_id_middleware(mut req: Request, next: Next) -> Response {
    let id = req
        .headers()
        .get("x-request-id")
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string())
        .unwrap_or_else(|| Uuid::new_v4().to_string());
    req.extensions_mut().insert(RequestId(id.clone()));
    let mut resp = next.run(req).await;
    if let Ok(v) = HeaderValue::from_str(&id) {
        resp.headers_mut().insert("x-request-id", v);
    }
    resp
}

async fn healthz() -> &'static str {
    "ok"
}

#[derive(Deserialize)]
pub struct IngestRequest {
    /// Load the snapshot from a local synthetic-proc directory.
    pub snapshot_dir: Option<String>,
    /// ...or supply the snapshot inline.
    pub snapshot: Option<Snapshot>,
}

#[derive(Serialize)]
pub struct IngestResponse {
    pub request_id: String,
    pub outcome: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub seq: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub procs: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub read_failures: Option<usize>,
}

impl IngestResponse {
    fn rejected(request_id: &str, reason: String) -> IngestResponse {
        IngestResponse {
            request_id: request_id.to_string(),
            outcome: "rejected".to_string(),
            reason: Some(reason),
            seq: None,
            procs: None,
            read_failures: None,
        }
    }
}

async fn ingest_snapshot(
    State(state): State<Shared>,
    Extension(RequestId(req_id)): Extension<RequestId>,
    Json(body): Json<IngestRequest>,
) -> Response {
    let snap: Snapshot = match (body.snapshot_dir, body.snapshot) {
        (Some(dir), None) => match snapshot::load_snapshot(&PathBuf::from(&dir)) {
            Ok(s) => s,
            Err(e) => {
                // Redacted: log the error kind, not the offending path/payload.
                tracing::warn!(request_id = %req_id, error = %e, "snapshot load failed");
                return (
                    StatusCode::BAD_REQUEST,
                    Json(IngestResponse::rejected(&req_id, format!("snapshot load failed: {e}"))),
                )
                    .into_response();
            }
        },
        (None, Some(s)) => s,
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(IngestResponse::rejected(
                    &req_id,
                    "exactly one of snapshot_dir or snapshot is required".to_string(),
                )),
            )
                .into_response();
        }
    };

    let outcome = {
        let mut engine = state.engine.lock().expect("engine mutex");
        engine.apply(&snap)
    };

    match outcome {
        IngestOutcome::Accepted { seq, procs, read_failures } => {
            if let Err(e) = state.store.append(&snap) {
                tracing::error!(request_id = %req_id, seq, error = %e, "journal append failed");
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(IngestResponse::rejected(&req_id, "journal append failed".to_string())),
                )
                    .into_response();
            }
            tracing::info!(request_id = %req_id, seq, procs, read_failures, "snapshot accepted");
            (
                StatusCode::ACCEPTED,
                Json(IngestResponse {
                    request_id: req_id,
                    outcome: "accepted".to_string(),
                    reason: None,
                    seq: Some(seq),
                    procs: Some(procs),
                    read_failures: Some(read_failures),
                }),
            )
                .into_response()
        }
        IngestOutcome::Rejected(reason) => {
            tracing::warn!(request_id = %req_id, reason = ?reason, "snapshot rejected");
            (
                StatusCode::CONFLICT,
                Json(IngestResponse::rejected(&req_id, format!("{reason:?}"))),
            )
                .into_response()
        }
    }
}

#[derive(Deserialize)]
pub struct DeltasQuery {
    pub pid: Option<Pid>,
}

#[derive(Serialize)]
pub struct DeltasResponse {
    pub request_id: String,
    pub deltas: Vec<CpuDelta>,
}

async fn list_deltas(
    State(state): State<Shared>,
    Extension(RequestId(req_id)): Extension<RequestId>,
    Query(q): Query<DeltasQuery>,
) -> Json<DeltasResponse> {
    let engine = state.engine.lock().expect("engine mutex");
    let deltas: Vec<CpuDelta> = match q.pid {
        Some(pid) => engine.deltas_for(pid).into_iter().cloned().collect(),
        None => engine.deltas().to_vec(),
    };
    Json(DeltasResponse { request_id: req_id, deltas })
}

#[derive(Serialize)]
pub struct TreeResponse {
    pub request_id: String,
    #[serde(flatten)]
    pub tree: TreeView,
}

async fn get_tree(
    State(state): State<Shared>,
    Extension(RequestId(req_id)): Extension<RequestId>,
) -> Json<TreeResponse> {
    let engine = state.engine.lock().expect("engine mutex");
    Json(TreeResponse { request_id: req_id, tree: engine.tree() })
}

#[derive(Serialize)]
pub struct UndeterminedResponse {
    pub request_id: String,
    pub intervals: Vec<UndeterminedInterval>,
}

async fn list_undetermined(
    State(state): State<Shared>,
    Extension(RequestId(req_id)): Extension<RequestId>,
) -> Json<UndeterminedResponse> {
    let engine = state.engine.lock().expect("engine mutex");
    Json(UndeterminedResponse {
        request_id: req_id,
        intervals: engine.undetermined().to_vec(),
    })
}
