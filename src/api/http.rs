//! HTTP boundary (Axum). Thin layer: parse, delegate to the service, map
//! decisions to status codes.
use axum::{
    extract::State,
    http::{header, HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use tower_http::limit::RequestBodyLimitLayer;

use crate::api::service::{run_arrow, run_json, ErrorResponse};
use crate::query::JoinRequest;
use crate::resource::{AppState, Decision};

pub fn router(state: AppState) -> Router {
    let body_limit = state.config.max_body_bytes;
    Router::new()
        .route("/health", get(health))
        .route("/api/v1/join", post(join_json))
        .route("/api/v1/join/arrow", post(join_arrow))
        .layer(RequestBodyLimitLayer::new(body_limit))
        .with_state(state)
}

async fn health() -> Json<serde_json::Value> {
    Json(serde_json::json!({ "status": "ok", "engine": "leapfrog-triejoin" }))
}

fn status_for(err: &ErrorResponse) -> StatusCode {
    match err.decision {
        Decision::Rejected => StatusCode::BAD_REQUEST,
        Decision::Undecidable => StatusCode::TOO_MANY_REQUESTS,
        Decision::Accepted => StatusCode::OK,
    }
}

impl IntoResponse for ErrorResponse {
    fn into_response(self) -> Response {
        let status = status_for(&self);
        (status, Json(self)).into_response()
    }
}

#[allow(clippy::result_large_err)] // ErrorResponse intentionally carries full diagnostics
async fn join_json(
    State(state): State<AppState>,
    Json(req): Json<JoinRequest>,
) -> Result<Json<crate::api::service::JoinResponse>, ErrorResponse> {
    Ok(Json(run_json(&state, req)?))
}

#[allow(clippy::result_large_err)]
async fn join_arrow(
    State(state): State<AppState>,
    Json(req): Json<JoinRequest>,
) -> Result<Response, ErrorResponse> {
    let (bytes, meta) = run_arrow(&state, req)?;
    let mut headers = HeaderMap::new();
    headers.insert(
        header::CONTENT_TYPE,
        "application/vnd.apache.arrow.stream".parse().unwrap(),
    );
    if let Ok(v) = header::HeaderValue::from_str(&meta.request_id) {
        headers.insert("X-LFJ-Request-Id", v);
    }
    headers.insert(
        "X-LFJ-Truncated",
        header::HeaderValue::from_static(if meta.truncated { "true" } else { "false" }),
    );
    if let Some(cursor) = &meta.next_cursor {
        if let Ok(v) = header::HeaderValue::from_str(cursor) {
            headers.insert("X-LFJ-Next-Cursor", v);
        }
    }
    Ok((StatusCode::OK, headers, bytes).into_response())
}
