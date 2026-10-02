//! HTTP entry points (Axum): request validation, execution, and diagnostics.
//!
//! Endpoints:
//!
//! | Method | Path                       | Purpose |
//! |--------|----------------------------|---------|
//! | POST   | `/v1/recursive/validate`   | Validation only — never executes the fixpoint |
//! | POST   | `/v1/recursive/execute`    | Validate and run |
//! | GET    | `/v1/version`              | Engine version |
//! | GET    | `/healthz`                 | Liveness |
//!
//! Every error response carries a concrete `failure_category`; an exception or
//! unknown condition is reported as `failed`, never as success.

use std::sync::Arc;

use axum::body::Bytes;
use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use tracing::{debug, warn};
use uuid::Uuid;

use crate::config::ServerConfig;
use crate::error::{EngineError, FailureCategory};
use crate::exec::engine::{execute, ENGINE_VERSION};
use crate::plan::{validate_request, ErrorResponse, RecursiveRequest, RunStatus};

/// Shared application state.
#[derive(Clone)]
pub struct AppState {
    pub config: Arc<ServerConfig>,
}

/// Build the application router with the configured body-size ceiling.
pub fn router(config: ServerConfig) -> Router {
    let max_bytes = config.max_request_bytes;
    let state = AppState {
        config: Arc::new(config),
    };
    Router::new()
        .route("/v1/recursive/validate", post(validate_handler))
        .route("/v1/recursive/execute", post(execute_handler))
        .route("/v1/version", get(version_handler))
        .route("/healthz", get(health_handler))
        .layer(axum::extract::DefaultBodyLimit::max(max_bytes))
        .with_state(state)
}

async fn version_handler() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "engine": "recursive-cte-backend",
        "version": ENGINE_VERSION,
    }))
}

async fn health_handler() -> Json<serde_json::Value> {
    Json(serde_json::json!({ "status": "ok", "version": ENGINE_VERSION }))
}

/// POST `/v1/recursive/validate`
async fn validate_handler(State(state): State<AppState>, body: Bytes) -> Response {
    let run_id = new_run_id();
    debug!(run_id = %run_id, bytes = body.len(), "validate request received");

    match parse_and_apply(state.config.as_ref(), &body, &run_id) {
        Ok(req) => {
            if let Err(err) = validate_request(&req) {
                categorized_error(&run_id, err)
            } else {
                (
                    StatusCode::OK,
                    Json(serde_json::json!({
                        "run_id": run_id,
                        "engine_version": ENGINE_VERSION,
                        "status": RunStatus::Complete,
                        "valid": true,
                    })),
                )
                    .into_response()
            }
        }
        Err(err) => categorized_error(&run_id, err),
    }
}

/// POST `/v1/recursive/execute`
async fn execute_handler(State(state): State<AppState>, body: Bytes) -> Response {
    let run_id = new_run_id();
    debug!(run_id = %run_id, bytes = body.len(), "execute request received");

    let req = match parse_and_apply(state.config.as_ref(), &body, &run_id) {
        Ok(req) => req,
        Err(err) => return categorized_error(&run_id, err),
    };

    // Run the synchronous, CPU-bound engine on the blocking pool so the async
    // runtime stays responsive.
    let run_id_owned = run_id.clone();
    let result = tokio::task::spawn_blocking(move || execute(&req, &run_id_owned))
        .await
        .map_err(|e| EngineError::internal(format!("executor task failed to join: {e}")));

    match result {
        Ok(Ok(response)) => Json(response).into_response(),
        Ok(Err(err)) => categorized_error(&run_id, err),
        Err(err) => categorized_error(&run_id, err),
    }
}

/// Parse JSON and apply server configuration (default limits / hard ceiling).
fn parse_and_apply(
    config: &ServerConfig,
    body: &[u8],
    run_id: &str,
) -> Result<RecursiveRequest, EngineError> {
    if body.is_empty() {
        return Err(EngineError::invalid_data("request body is empty"));
    }
    let mut req: RecursiveRequest = serde_json::from_slice(body)?;
    config.enforce(&mut req).map_err(|e| {
        warn!(run_id = %run_id, error = %e, "request rejected by server policy");
        e
    })?;
    Ok(req)
}

fn new_run_id() -> String {
    format!("run-{}-{}", chrono_compact(), Uuid::new_v4().simple())
}

/// Minimal timestamp segment for run IDs without pulling a time dependency
/// beyond std (milliseconds since UNIX epoch).
fn chrono_compact() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    format!("{ms:x}")
}

fn categorized_error(run_id: &str, err: EngineError) -> Response {
    warn!(run_id = %run_id, category = ?err.category, error = %err.message, "request failed");
    let status = match err.category {
        FailureCategory::InvalidPlan => StatusCode::BAD_REQUEST,
        FailureCategory::InvalidData => StatusCode::UNPROCESSABLE_ENTITY,
        FailureCategory::ResourceLimit => StatusCode::UNPROCESSABLE_ENTITY,
        FailureCategory::Internal => StatusCode::INTERNAL_SERVER_ERROR,
    };
    let body = ErrorResponse {
        run_id: run_id.to_string(),
        engine_version: ENGINE_VERSION.to_string(),
        status: RunStatus::Failed,
        failure_category: err.category,
        message: err.message,
    };
    (status, Json(body)).into_response()
}
