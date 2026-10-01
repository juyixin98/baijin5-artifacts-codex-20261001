//! Axum HTTP surface. Thin transport over [`crate::validate`]; all contract
//! logic stays in the shared entry point so CLI and tests exercise identical
//! code.

use axum::extract::State;
use axum::http::StatusCode;
use axum::routing::{get, post};
use axum::{Json, Router};
use serde_json::json;

use crate::state::{AppState, DiagRecord};
use crate::validate::{verify, VerifyRequest};

pub fn router(state: std::sync::Arc<AppState>) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/verify", post(verify_handler))
        .route("/diagnostics", get(diagnostics_handler))
        .with_state(state)
}

async fn health() -> Json<serde_json::Value> {
    Json(json!({ "status": "ok", "service": "collation-agg-contract" }))
}

async fn verify_handler(
    State(state): State<std::sync::Arc<AppState>>,
    Json(req): Json<VerifyRequest>,
) -> (StatusCode, Json<serde_json::Value>) {
    // Rejection is an application verdict (HTTP 200 with a categorized body) so
    // clients branch on `category`, not transport status. Only operator faults
    // surface as 5xx.
    match verify(&state, req) {
        Ok(report) => (StatusCode::OK, Json(json!(report))),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({
                "status": "error",
                "reason_code": "operator_fault",
                "detail": e.to_string(),
            })),
        ),
    }
}

#[derive(serde::Serialize)]
struct DiagView {
    count: usize,
    records: Vec<DiagRecord>,
}

async fn diagnostics_handler(State(state): State<std::sync::Arc<AppState>>) -> Json<DiagView> {
    let records = state.diag.recent(50);
    let count = records.len();
    Json(DiagView { count, records })
}
