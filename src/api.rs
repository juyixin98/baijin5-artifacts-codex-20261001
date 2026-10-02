//! Diagnostics interface: Axum HTTP API over the merge engine and store.
//!
//! Every response is explainable: reports carry run_id + request_id, the
//! engine version, per-layer processing records, and failures and
//! uncertain conclusions in separate lists. Errors use a consistent
//! envelope with a machine-readable code.

use crate::config::AppConfig;
use crate::merge::{EngineOptions, MergeEngine, MergeError};
use crate::model::*;
use crate::store::{RunStore, StoreError};
use axum::extract::{Path as AxumPath, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::IntoResponse;
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::Serialize;
use std::path::PathBuf;
use std::sync::Arc;
use time::OffsetDateTime;
use uuid::Uuid;

/// Shared application state.
pub struct AppState {
    pub config: AppConfig,
    pub store: RunStore,
    pub engine: MergeEngine,
}

pub fn build_router(config: AppConfig) -> Router {
    let store = RunStore::new(&config.state_dir);
    let engine = MergeEngine::new(EngineOptions {
        max_symlink_depth: config.max_symlink_depth,
        max_entries: config.max_entries,
    });
    let state = Arc::new(AppState {
        config,
        store,
        engine,
    });
    Router::new()
        .route("/v1/health", get(health))
        .route("/v1/version", get(version))
        .route("/v1/merges", post(create_merge).get(list_merges))
        .route("/v1/merges/{run_id}", get(get_merge))
        .route("/v1/merges/{run_id}/entries", get(get_entries))
        .route("/v1/merges/{run_id}/diagnostics", get(get_diagnostics))
        .with_state(state)
}

/// Consistent error envelope.
#[derive(Debug, Serialize)]
struct ErrorBody {
    error: ErrorDetail,
}

#[derive(Debug, Serialize)]
struct ErrorDetail {
    code: &'static str,
    message: String,
    /// Correlation id of the request that failed, when known.
    #[serde(skip_serializing_if = "Option::is_none")]
    request_id: Option<String>,
}

struct ApiError {
    status: StatusCode,
    body: ErrorBody,
}

impl ApiError {
    fn new(status: StatusCode, code: &'static str, message: impl Into<String>) -> Self {
        Self {
            status,
            body: ErrorBody {
                error: ErrorDetail {
                    code,
                    message: message.into(),
                    request_id: None,
                },
            },
        }
    }

    fn with_request_id(mut self, request_id: Option<String>) -> Self {
        self.body.error.request_id = request_id;
        self
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> axum::response::Response {
        (self.status, Json(self.body)).into_response()
    }
}

fn now_rfc3339() -> String {
    OffsetDateTime::now_utc()
        .format(&time::format_description::well_known::Rfc3339)
        .unwrap_or_else(|_| "unknown".to_string())
}

fn correlation_id(headers: &HeaderMap, body_id: Option<&str>) -> Option<String> {
    body_id
        .map(str::to_string)
        .or_else(|| headers.get("x-request-id").and_then(|v| v.to_str().ok()).map(str::to_string))
}

#[derive(Serialize)]
struct Health {
    status: &'static str,
}

async fn health() -> Json<Health> {
    Json(Health { status: "ok" })
}

#[derive(Serialize)]
struct Version {
    engine_version: &'static str,
    report_schema_version: u32,
}

async fn version() -> Json<Version> {
    Json(Version {
        engine_version: ENGINE_VERSION,
        report_schema_version: REPORT_SCHEMA_VERSION,
    })
}

#[derive(Serialize)]
struct CreateMergeResponse {
    run_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    request_id: Option<String>,
    engine_version: &'static str,
    entries: usize,
    failures: usize,
    uncertainties: usize,
    stats: MergeStats,
}

async fn create_merge(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(req): Json<MergeRequest>,
) -> Result<(StatusCode, Json<CreateMergeResponse>), ApiError> {
    let request_id = correlation_id(&headers, req.request_id.as_deref());
    let run_id = Uuid::new_v4().to_string();
    let fail = |status, code, msg: String| {
        Err(ApiError::new(status, code, msg).with_request_id(request_id.clone()))
    };

    if req.layers.is_empty() {
        return fail(
            StatusCode::BAD_REQUEST,
            "no_layers",
            "request must name at least one layer".to_string(),
        );
    }

    // Resolve and authorize every layer path before touching the engine:
    // nothing outside the configured layer roots is ever read.
    let mut inputs = Vec::with_capacity(req.layers.len());
    for (i, spec) in req.layers.iter().enumerate() {
        let (id, raw) = match spec {
            LayerSpec::Path(p) => (format!("layer-{i}"), p.clone()),
            LayerSpec::Named { id, path } => (id.clone(), path.clone()),
        };
        let path = PathBuf::from(&raw);
        if !path.is_dir() {
            return fail(
                StatusCode::BAD_REQUEST,
                "layer_unavailable",
                format!("layer {i} ({id}) is not a readable directory: {raw}"),
            );
        }
        if !state.config.layer_path_allowed(&path) {
            return fail(
                StatusCode::FORBIDDEN,
                "layer_outside_roots",
                format!("layer {i} ({id}) resolves outside the configured layer roots: {raw}"),
            );
        }
        inputs.push(LayerInput { id, root: path });
    }

    let started_at = now_rfc3339();
    tracing::info!(
        run_id = %run_id,
        request_id = request_id.as_deref().unwrap_or("-"),
        layers = inputs.len(),
        "merge run started"
    );

    let output = state.engine.merge(&inputs).map_err(|e| match e {
        MergeError::LayerUnavailable { layer, path } => ApiError::new(
            StatusCode::BAD_REQUEST,
            "layer_unavailable",
            format!("layer {layer} unavailable: {}", path.display()),
        ),
        MergeError::EntryLimitExceeded { limit } => ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "entry_limit_exceeded",
            format!("scanned entries exceed the configured limit of {limit}"),
        ),
        MergeError::Io { layer, source } => ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "layer_io_error",
            format!("I/O error scanning layer {layer}: {source}"),
        ),
    }.with_request_id(request_id.clone()))?;

    let report = MergeReport {
        schema_version: REPORT_SCHEMA_VERSION,
        engine_version: ENGINE_VERSION.to_string(),
        run_id: run_id.clone(),
        request_id: request_id.clone(),
        started_at,
        finished_at: now_rfc3339(),
        layers: output.layers,
        entries: output.entries,
        failures: output.failures,
        uncertainties: output.uncertainties,
        stats: output.stats,
    };

    state.store.save(&report).map_err(|e| {
        ApiError::new(
            StatusCode::INTERNAL_SERVER_ERROR,
            "store_error",
            format!("failed to persist run: {e}"),
        )
        .with_request_id(request_id.clone())
    })?;

    tracing::info!(
        run_id = %run_id,
        request_id = request_id.as_deref().unwrap_or("-"),
        entries = report.entries.len(),
        failures = report.failures.len(),
        uncertainties = report.uncertainties.len(),
        "merge run finished and persisted"
    );

    Ok((
        StatusCode::CREATED,
        Json(CreateMergeResponse {
            run_id,
            request_id,
            engine_version: ENGINE_VERSION,
            entries: report.entries.len(),
            failures: report.failures.len(),
            uncertainties: report.uncertainties.len(),
            stats: report.stats,
        }),
    ))
}

async fn list_merges(
    State(state): State<Arc<AppState>>,
) -> Result<Json<Vec<crate::store::RunSummary>>, ApiError> {
    state.store.list().map(Json).map_err(|e| {
        ApiError::new(
            StatusCode::INTERNAL_SERVER_ERROR,
            "store_error",
            format!("failed to list runs: {e}"),
        )
    })
}

fn load_run(state: &AppState, run_id: &str) -> Result<MergeReport, ApiError> {
    match state.store.load(run_id) {
        Ok(Some(report)) => Ok(report),
        Ok(None) => Err(ApiError::new(
            StatusCode::NOT_FOUND,
            "run_not_found",
            format!("no merge run with id {run_id}"),
        )),
        Err(StoreError::InvalidRunId(_)) => Err(ApiError::new(
            StatusCode::BAD_REQUEST,
            "invalid_run_id",
            format!("run id {run_id:?} is not a valid id"),
        )),
        Err(e) => Err(ApiError::new(
            StatusCode::INTERNAL_SERVER_ERROR,
            "store_error",
            format!("failed to load run: {e}"),
        )),
    }
}

async fn get_merge(
    State(state): State<Arc<AppState>>,
    AxumPath(run_id): AxumPath<String>,
) -> Result<Json<MergeReport>, ApiError> {
    load_run(&state, &run_id).map(Json)
}

async fn get_entries(
    State(state): State<Arc<AppState>>,
    AxumPath(run_id): AxumPath<String>,
) -> Result<Json<Vec<FinalEntry>>, ApiError> {
    let report = load_run(&state, &run_id)?;
    Ok(Json(report.entries.into_values().collect()))
}

#[derive(Serialize)]
struct DiagnosticsView {
    run_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    request_id: Option<String>,
    engine_version: String,
    failures: Vec<Diagnostic>,
    uncertainties: Vec<Diagnostic>,
}

async fn get_diagnostics(
    State(state): State<Arc<AppState>>,
    AxumPath(run_id): AxumPath<String>,
) -> Result<Json<DiagnosticsView>, ApiError> {
    let report = load_run(&state, &run_id)?;
    Ok(Json(DiagnosticsView {
        run_id: report.run_id,
        request_id: report.request_id,
        engine_version: report.engine_version,
        failures: report.failures,
        uncertainties: report.uncertainties,
    }))
}
