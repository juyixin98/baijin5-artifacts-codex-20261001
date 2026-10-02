//! Diagnostics HTTP API (Axum). Errors are categorized and mapped to
//! distinct status codes; unknown or exceptional states are never reported
//! as success.

use std::sync::Arc;

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::{Deserialize, Serialize};

use crate::metrics::{build_report, RunReport};
use crate::persist::{RunStore, StoreError};
use crate::scenario::{find_builtin, Scenario, ScenarioError};
use crate::scheduler::{Engine, RunFailure, Sample};
use crate::VERSION;

pub struct AppState {
    pub store: RunStore,
}

#[derive(Debug, Serialize)]
struct ErrorBody {
    error: ErrorDetail,
}

#[derive(Debug, Serialize)]
struct ErrorDetail {
    kind: &'static str,
    message: String,
}

#[derive(Debug)]
pub enum ApiError {
    UnknownScenario(String),
    InvalidScenario(String),
    InvalidRequest(String),
    RunNotFound(String),
    InvalidRunId(String),
    RunFailed { run_id: String, reason: String },
    Store(String),
}

impl ApiError {
    fn kind(&self) -> &'static str {
        match self {
            ApiError::UnknownScenario(_) => "unknown_scenario",
            ApiError::InvalidScenario(_) => "invalid_scenario",
            ApiError::InvalidRequest(_) => "invalid_request",
            ApiError::RunNotFound(_) => "not_found",
            ApiError::InvalidRunId(_) => "invalid_run_id",
            ApiError::RunFailed { .. } => "run_failed",
            ApiError::Store(_) => "store_error",
        }
    }

    fn status(&self) -> StatusCode {
        match self {
            ApiError::UnknownScenario(_) => StatusCode::BAD_REQUEST,
            ApiError::InvalidScenario(_) => StatusCode::BAD_REQUEST,
            ApiError::InvalidRequest(_) => StatusCode::BAD_REQUEST,
            ApiError::RunNotFound(_) => StatusCode::NOT_FOUND,
            ApiError::InvalidRunId(_) => StatusCode::BAD_REQUEST,
            ApiError::RunFailed { .. } => StatusCode::UNPROCESSABLE_ENTITY,
            ApiError::Store(_) => StatusCode::INTERNAL_SERVER_ERROR,
        }
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let status = self.status();
        let body = ErrorBody {
            error: ErrorDetail {
                kind: self.kind(),
                message: self.to_string(),
            },
        };
        (status, Json(body)).into_response()
    }
}

impl std::fmt::Display for ApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ApiError::UnknownScenario(n) => write!(f, "unknown scenario '{n}'"),
            ApiError::InvalidScenario(m) => write!(f, "invalid scenario: {m}"),
            ApiError::InvalidRequest(m) => write!(f, "invalid request: {m}"),
            ApiError::RunNotFound(id) => write!(f, "run '{id}' not found"),
            ApiError::InvalidRunId(id) => write!(f, "invalid run id '{id}'"),
            ApiError::RunFailed { run_id, reason } => {
                write!(f, "run '{run_id}' failed: {reason}")
            }
            ApiError::Store(m) => write!(f, "store error: {m}"),
        }
    }
}

impl From<StoreError> for ApiError {
    fn from(e: StoreError) -> Self {
        match e {
            StoreError::NotFound { run_id } => ApiError::RunNotFound(run_id),
            StoreError::InvalidRunId(id) => ApiError::InvalidRunId(id),
            other => ApiError::Store(other.to_string()),
        }
    }
}

#[derive(Debug, Serialize)]
struct Health {
    status: &'static str,
    version: &'static str,
}

#[derive(Debug, Serialize)]
struct VersionInfo {
    name: &'static str,
    version: &'static str,
    model: &'static str,
}

#[derive(Debug, Serialize)]
struct ScenarioInfo {
    name: String,
    description: String,
    duration_ms: u64,
    tasks: usize,
}

#[derive(Debug, Deserialize)]
pub struct CreateRunRequest {
    /// Name of a compiled-in scenario.
    pub scenario: Option<String>,
    /// Inline scenario definition (takes precedence over `scenario`).
    pub spec: Option<Scenario>,
}

#[derive(Debug, Serialize)]
struct CreateRunResponse {
    run_id: String,
    status: crate::metrics::RunStatus,
    passed: bool,
}

pub fn router(state: Arc<AppState>) -> Router {
    Router::new()
        .route("/healthz", get(healthz))
        .route("/api/version", get(version))
        .route("/api/scenarios", get(list_scenarios))
        .route("/api/runs", post(create_run).get(list_runs))
        .route("/api/runs/{run_id}", get(get_run))
        .route("/api/runs/{run_id}/samples", get(get_samples))
        .with_state(state)
}

async fn healthz() -> Json<Health> {
    Json(Health {
        status: "ok",
        version: VERSION,
    })
}

async fn version() -> Json<VersionInfo> {
    Json(VersionInfo {
        name: "cfs-sim",
        version: VERSION,
        model: "single-CPU CFS-style fair scheduler, tick-based virtual clock",
    })
}

async fn list_scenarios() -> Json<Vec<ScenarioInfo>> {
    Json(
        crate::scenario::builtin_scenarios()
            .into_iter()
            .map(|s| ScenarioInfo {
                name: s.name,
                description: s.description,
                duration_ms: s.duration_ms,
                tasks: s.tasks.len(),
            })
            .collect(),
    )
}

/// Execute one scenario and persist the artifacts. The simulation runs in
/// virtual time, so this completes synchronously.
pub fn execute_scenario(
    store: &RunStore,
    scenario: &Scenario,
) -> Result<(String, RunReport), ApiError> {
    scenario
        .validate()
        .map_err(|e: ScenarioError| ApiError::InvalidScenario(e.to_string()))?;
    let run_id = format!("run-{}", uuid::Uuid::new_v4().simple());
    let engine = Engine::new(
        scenario.config,
        scenario.tasks.clone(),
        scenario.duration_ms,
    )
    .map_err(|e| ApiError::InvalidScenario(e.to_string()))?;
    match engine.run() {
        Ok(outcome) => {
            let samples = outcome.samples.clone();
            let report = build_report(&run_id, scenario, &outcome, None);
            store.save(&run_id, &scenario.name, &report, &samples)?;
            Ok((run_id, report))
        }
        Err(RunFailure::ExceededMaxTicks { max_ticks }) => {
            // Persist the failure as a failed run rather than dropping it,
            // and report a distinct error category to the caller.
            let reason = format!("exceeded max tick budget of {max_ticks}");
            let outcome = crate::scheduler::RunOutcome {
                elapsed_ms: 0,
                idle_ms: 0,
                busy_ms: 0,
                min_vruntime: 0,
                tasks: Vec::new(),
                events: Vec::new(),
                samples: Vec::new(),
            };
            let report = build_report(&run_id, scenario, &outcome, Some(reason.clone()));
            let _ = store.save(&run_id, &scenario.name, &report, &[]);
            Err(ApiError::RunFailed { run_id, reason })
        }
    }
}

async fn create_run(
    State(state): State<Arc<AppState>>,
    Json(req): Json<CreateRunRequest>,
) -> Result<(StatusCode, Json<CreateRunResponse>), ApiError> {
    let scenario = match (req.spec, req.scenario) {
        (Some(spec), _) => {
            spec.validate()
                .map_err(|e| ApiError::InvalidScenario(e.to_string()))?;
            spec
        }
        (None, Some(name)) => {
            find_builtin(&name).map_err(|_| ApiError::UnknownScenario(name))?
        }
        (None, None) => {
            return Err(ApiError::InvalidRequest(
                "provide either 'scenario' (builtin name) or 'spec' (inline definition)"
                    .to_string(),
            ))
        }
    };
    let (run_id, report) = execute_scenario(&state.store, &scenario)?;
    Ok((
        StatusCode::CREATED,
        Json(CreateRunResponse {
            run_id,
            status: report.status,
            passed: report.passed,
        }),
    ))
}

async fn list_runs(
    State(state): State<Arc<AppState>>,
) -> Result<Json<Vec<crate::persist::RunSummary>>, ApiError> {
    Ok(Json(state.store.list()?))
}

async fn get_run(
    State(state): State<Arc<AppState>>,
    Path(run_id): Path<String>,
) -> Result<Json<RunReport>, ApiError> {
    Ok(Json(state.store.load_report(&run_id)?))
}

async fn get_samples(
    State(state): State<Arc<AppState>>,
    Path(run_id): Path<String>,
) -> Result<Json<Vec<Sample>>, ApiError> {
    Ok(Json(state.store.load_samples(&run_id)?))
}
