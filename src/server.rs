//! Axum HTTP server exposing the validation/execution boundary.

use std::sync::Arc;

use axum::{
    extract::State,
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::get,
    Json, Router,
};
use serde_json::json;
use tracing::{info, warn};

use crate::api::service::{error_response, run_pipeline};
use crate::api::ExecuteRequest;
use crate::config::Config;

/// Shared application state.
#[derive(Clone)]
pub struct AppState {
    /// Process configuration and limit ceilings.
    pub config: Arc<Config>,
}

/// Build the router (also used by integration tests).
pub fn router(config: Config) -> Router {
    Router::new()
        .route("/", get(index))
        .route("/health", get(health))
        .route("/execute", axum::routing::post(execute))
        .with_state(AppState {
            config: Arc::new(config),
        })
}

async fn index() -> Json<serde_json::Value> {
    Json(json!({
        "service": "recursive-cte",
        "version": crate::state::ENGINE_VERSION,
        "endpoints": {
            "health": "GET /health",
            "execute": "POST /execute"
        }
    }))
}

async fn health() -> Json<serde_json::Value> {
    Json(json!({ "status": "ok", "version": crate::state::ENGINE_VERSION }))
}

async fn execute(State(state): State<AppState>, body: String) -> Response {
    // Parse failure is an explicit validation_error, never a silent success.
    let req: ExecuteRequest = match serde_json::from_str(&body) {
        Ok(req) => req,
        Err(err) => {
            warn!(error = %err, "malformed request body");
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({
                    "outcome": "error",
                    "category": "validation_error",
                    "message": format!("malformed JSON request: {err}"),
                    "run_id": serde_json::Value::Null,
                })),
            )
                .into_response();
        }
    };

    match run_pipeline(req, &state.config) {
        Ok(resp) => {
            info!(
                run_id = %resp.run_id,
                status = %resp.status,
                rows = resp.row_count,
                rounds = resp.rounds,
                "execution finished"
            );
            Json(resp).into_response()
        }
        Err(err) => {
            let (code, envelope) = error_response(&err);
            warn!(category = %envelope.category, error = %err, "execution rejected");
            (
                StatusCode::from_u16(code).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
                Json(envelope),
            )
                .into_response()
        }
    }
}
