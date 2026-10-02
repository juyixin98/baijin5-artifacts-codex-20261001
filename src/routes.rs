//! HTTP diagnostic interface (axum).
//!
//! Endpoints:
//! * `GET  /v1/health`           — name/version liveness.
//! * `POST /v1/merges`           — run a merge; body `{"layers": [...], "output_dir": "..."}`.
//! * `GET  /v1/merges/{run_id}`  — fetch a persisted run record (JSON).
//! * `GET  /v1/merges/{run_id}/report` — same run, human-readable text.
//!
//! Every request carries a request identity: the caller's `x-request-id`
//! header, or a generated `req-<uuid>`. It is echoed in the response header
//! and embedded in the run record and logs.

use crate::config::Config;
use crate::error::{FailureCategory, MergeError};
use crate::merge::{MergeEngine, MergeRequest};
use crate::model::{MergeRun, TOOL_VERSION};
use crate::store::RunStore;
use axum::extract::{Path as AxumPath, State};
use axum::http::{HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::sync::Arc;

#[derive(Clone)]
pub struct AppState {
    pub config: Arc<Config>,
    pub store: Arc<RunStore>,
}

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/v1/health", get(health))
        .route("/v1/merges", post(create_merge))
        .route("/v1/merges/{run_id}", get(get_merge))
        .route("/v1/merges/{run_id}/report", get(get_merge_report))
        .with_state(state)
}

#[derive(Serialize)]
struct Health {
    name: &'static str,
    version: &'static str,
}

async fn health() -> Json<Health> {
    Json(Health {
        name: "oci-layer-merge-checker",
        version: TOOL_VERSION,
    })
}

#[derive(Deserialize)]
pub struct CreateMergeBody {
    /// Layer directories, bottom first.
    pub layers: Vec<String>,
    /// Destination for the final tree; must not exist or be empty.
    pub output_dir: String,
}

#[derive(Serialize)]
struct ApiErrorBody {
    request_id: String,
    error: ApiErrorDetail,
}

#[derive(Serialize)]
struct ApiErrorDetail {
    category: FailureCategory,
    message: String,
}

fn api_error(status: StatusCode, request_id: &str, err: MergeError) -> Response {
    let body = ApiErrorBody {
        request_id: request_id.to_string(),
        error: ApiErrorDetail {
            category: err.category,
            message: err.message,
        },
    };
    let mut resp = (status, Json(body)).into_response();
    resp.headers_mut().insert(
        "x-request-id",
        HeaderValue::from_str(request_id).unwrap_or(HeaderValue::from_static("invalid")),
    );
    resp
}

/// Extract or generate the request identity.
fn request_id(headers: &HeaderMap) -> String {
    headers
        .get("x-request-id")
        .and_then(|v| v.to_str().ok())
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| format!("req-{}", uuid::Uuid::new_v4()))
}

async fn create_merge(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<CreateMergeBody>,
) -> Response {
    let req_id = request_id(&headers);
    let root = state.config.workspace_root.clone();

    // Validate every path against the isolation root before touching disk.
    let validated = (|| -> Result<(Vec<PathBuf>, PathBuf), MergeError> {
        if body.layers.is_empty() {
            return Err(MergeError::new(
                FailureCategory::InvalidPath,
                "at least one layer is required",
            ));
        }
        let mut layers = Vec::with_capacity(body.layers.len());
        for l in &body.layers {
            layers.push(crate::paths::ensure_within_root(&root, PathBuf::from(l).as_path())?);
        }
        let output = crate::paths::ensure_within_root(&root, PathBuf::from(&body.output_dir).as_path())?;
        Ok((layers, output))
    })();
    let (layers, output_dir) = match validated {
        Ok(v) => v,
        Err(e) => {
            tracing::warn!(request_id = %req_id, category = %e.category, "request rejected");
            return api_error(StatusCode::BAD_REQUEST, &req_id, e);
        }
    };

    let run_id = format!("run-{}", uuid::Uuid::new_v4());
    let store = state.store.clone();
    let req_id_for_run = req_id.clone();
    let run = tokio::task::spawn_blocking(move || {
        let engine = MergeEngine::new();
        engine.execute(&MergeRequest {
            run_id,
            request_id: req_id_for_run,
            layers,
            output_dir,
        })
    })
    .await;
    let run = match run {
        Ok(r) => r,
        Err(e) => {
            return api_error(
                StatusCode::INTERNAL_SERVER_ERROR,
                &req_id,
                MergeError::new(FailureCategory::Io, format!("merge task failed: {e}")),
            )
        }
    };

    if let Err(e) = store.append(&run) {
        tracing::error!(request_id = %req_id, "persist failed: {e}");
        return api_error(StatusCode::INTERNAL_SERVER_ERROR, &req_id, e);
    }
    tracing::info!(
        request_id = %req_id,
        run_id = %run.run_id,
        status = ?run.status,
        entries = run.entries.len(),
        failures = run.failures.len(),
        uncertainties = run.uncertainties.len(),
        "merge run completed"
    );
    for line in crate::diag::summarize(&run).lines() {
        tracing::info!(request_id = %req_id, run_id = %run.run_id, "{line}");
    }

    let mut resp = (StatusCode::OK, Json(run)).into_response();
    resp.headers_mut().insert(
        "x-request-id",
        HeaderValue::from_str(&req_id).unwrap_or(HeaderValue::from_static("invalid")),
    );
    resp
}

async fn get_merge(
    State(state): State<AppState>,
    headers: HeaderMap,
    AxumPath(run_id): AxumPath<String>,
) -> Response {
    let req_id = request_id(&headers);
    match state.store.get(&run_id) {
        Ok(Some(run)) => {
            let mut resp = (StatusCode::OK, Json(run)).into_response();
            resp.headers_mut().insert(
                "x-request-id",
                HeaderValue::from_str(&req_id).unwrap_or(HeaderValue::from_static("invalid")),
            );
            resp
        }
        Ok(None) => api_error(
            StatusCode::NOT_FOUND,
            &req_id,
            MergeError::new(FailureCategory::RunNotFound, format!("run {run_id} not found")),
        ),
        Err(e) => api_error(StatusCode::INTERNAL_SERVER_ERROR, &req_id, e),
    }
}

async fn get_merge_report(
    State(state): State<AppState>,
    headers: HeaderMap,
    AxumPath(run_id): AxumPath<String>,
) -> Response {
    let req_id = request_id(&headers);
    match state.store.get(&run_id) {
        Ok(Some(run)) => {
            let text = crate::diag::summarize(&run);
            let mut resp = (StatusCode::OK, text).into_response();
            resp.headers_mut().insert(
                "x-request-id",
                HeaderValue::from_str(&req_id).unwrap_or(HeaderValue::from_static("invalid")),
            );
            resp
        }
        Ok(None) => api_error(
            StatusCode::NOT_FOUND,
            &req_id,
            MergeError::new(FailureCategory::RunNotFound, format!("run {run_id} not found")),
        ),
        Err(e) => api_error(StatusCode::INTERNAL_SERVER_ERROR, &req_id, e),
    }
}

/// Convenience for tests and the binary: run a merge end-to-end against a
/// store, returning the persisted run.
pub async fn run_merge_for_test(
    state: AppState,
    layers: Vec<PathBuf>,
    output_dir: PathBuf,
) -> MergeRun {
    let run_id = format!("run-{}", uuid::Uuid::new_v4());
    let run = tokio::task::spawn_blocking(move || {
        MergeEngine::new().execute(&MergeRequest {
            run_id,
            request_id: "req-test".to_string(),
            layers,
            output_dir,
        })
    })
    .await
    .expect("merge task");
    state.store.append(&run).expect("persist run");
    run
}
