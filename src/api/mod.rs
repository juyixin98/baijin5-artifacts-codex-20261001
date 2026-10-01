//! HTTP surface (axum). Thin transport wrapper over the pure validation pipeline.

use axum::{
    body::Bytes,
    extract::State,
    http::StatusCode,
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use serde::Serialize;

use crate::diag::FailureCategory;
use crate::service::{run_comparison, GroupRequest, Verdict};
use crate::state::AppState;

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/api/v1/group", post(group))
        .route("/api/v1/validate", post(group))
        .with_state(state)
}

async fn health() -> Json<serde_json::Value> {
    Json(serde_json::json!({ "status": "ok", "service": "collate_agg" }))
}

async fn group(State(state): State<AppState>, body: Bytes) -> axum::response::Response {
    let req: GroupRequest = match serde_json::from_slice(&body) {
        Ok(req) => req,
        Err(e) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(ApiError {
                    error: format!("invalid request body: {e}"),
                }),
            )
                .into_response();
        }
    };
    let verdict = run_comparison(&state, req);
    Response::from(verdict).into_response()
}

#[derive(Debug)]
struct Response {
    status: StatusCode,
    body: Verdict,
}

impl From<Verdict> for Response {
    fn from(v: Verdict) -> Self {
        use crate::diag::Decision::*;
        let status = match v.decision {
            Accepted => StatusCode::OK,
            Rejected => status_for_failure(v.failure.as_ref()),
            Undetermined => StatusCode::UNPROCESSABLE_ENTITY,
        };
        Self { status, body: v }
    }
}

fn status_for_failure(failure: Option<&FailureCategory>) -> StatusCode {
    match failure {
        // Caller asked for something invalid -> 4xx, not 5xx.
        Some(FailureCategory::UnknownRuleVersion { .. }) => StatusCode::BAD_REQUEST,
        Some(FailureCategory::RuleVersionMixed { .. }) => StatusCode::CONFLICT,
        Some(FailureCategory::EmptyColumn | FailureCategory::InvalidRow { .. }) => {
            StatusCode::UNPROCESSABLE_ENTITY
        }
        // Our two executors/oracle disagree: an internal contract violation.
        Some(FailureCategory::ExecutorMismatch { .. } | FailureCategory::OracleMismatch { .. }) => {
            StatusCode::INTERNAL_SERVER_ERROR
        }
        None => StatusCode::OK,
    }
}

impl IntoResponse for Response {
    fn into_response(self) -> axum::response::Response {
        (self.status, Json(self.body)).into_response()
    }
}

/// Malformed JSON body -> 400 with a stable error envelope.
#[derive(Debug, Serialize)]
pub struct ApiError {
    pub error: String,
}
