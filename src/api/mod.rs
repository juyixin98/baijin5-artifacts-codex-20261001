//! Axum HTTP adapter. Exposes three endpoints:
//!
//! * `POST /v1/join`          — one-shot IEJoin,
//! * `POST /v1/cursors`       — open a batched cursor (returns page 1),
//! * `POST /v1/cursors/next`  — fetch the next controlled page.
//!
//! Error categories map to HTTP status at this boundary
//! (400/409/413/500). The JSON bodies use the DTO contract in
//! [`crate::dto`]; malformed JSON is itself an `Input` error.

use std::sync::Arc;

use axum::body::Bytes;
use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::de::DeserializeOwned;

use crate::dto::{
    CursorOpenRequest, CursorPageRequest, CursorPageResponse, ErrorResponse, JoinRequest,
    JoinResponse,
};
use crate::engine;
use crate::error::{ErrorCategory, JoinError};
use crate::state::SessionStore;
use crate::trace::Tracer;

#[derive(Clone)]
pub struct AppState {
    pub tracer: Arc<Tracer>,
    pub sessions: Arc<SessionStore>,
}

impl AppState {
    #[must_use]
    pub fn new(tracer: Tracer, sessions: SessionStore) -> Self {
        Self {
            tracer: Arc::new(tracer),
            sessions: Arc::new(sessions),
        }
    }
}

/// Build the application router.
pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/healthz", get(healthz))
        .route("/v1/join", post(join))
        .route("/v1/cursors", post(open_cursor))
        .route("/v1/cursors/next", post(next_cursor))
        .with_state(state)
}

async fn healthz() -> Json<serde_json::Value> {
    Json(serde_json::json!({"status": "ok"}))
}

async fn join(State(st): State<AppState>, body: Bytes) -> Result<Json<JoinResponse>, ApiError> {
    let req: JoinRequest = parse_json(&body).map_err(ApiError::from)?;
    let parsed = req.parse().map_err(ApiError::from)?;
    let out = engine::execute_oneshot(parsed, &st.tracer).map_err(ApiError::from)?;
    Ok(Json(JoinResponse {
        run_id: out.run_id,
        pairs: out.pair_dtos,
        count: out.pairs.len(),
        truncated: out.truncated,
        counters: out.counters.into(),
        events: out.events,
    }))
}

async fn open_cursor(
    State(st): State<AppState>,
    body: Bytes,
) -> Result<Json<CursorPageResponse>, ApiError> {
    let req: CursorOpenRequest = parse_json(&body).map_err(ApiError::from)?;
    let CursorOpenRequest { join, page_size } = req;
    let parsed = join.parse().map_err(ApiError::from)?;
    let opened =
        engine::open_cursor(parsed, page_size, &st.sessions, &st.tracer).map_err(ApiError::from)?;
    Ok(Json(opened.page))
}

async fn next_cursor(
    State(st): State<AppState>,
    body: Bytes,
) -> Result<Json<CursorPageResponse>, ApiError> {
    let req: CursorPageRequest = parse_json(&body).map_err(ApiError::from)?;
    let CursorPageRequest {
        cursor_id,
        page_size,
    } = req;
    let page =
        engine::advance(cursor_id, page_size, &st.sessions, &st.tracer).map_err(ApiError::from)?;
    Ok(Json(page))
}

/// Newtype so `?` can convert [`JoinError`] into an HTTP response while
/// malformed JSON bodies are normalized to `Input`.
struct ApiError(JoinError);

impl From<JoinError> for ApiError {
    fn from(e: JoinError) -> Self {
        Self(e)
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let status = StatusCode::from_u16(self.0.category.http_status())
            .unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
        let body = ErrorResponse::from(&self.0);
        if self.0.category == ErrorCategory::Compute {
            tracing::error!(error = %self.0, "compute failure");
        }
        (status, Json(body)).into_response()
    }
}

/// Axum `Json` extractor rejects malformed bodies with its own rejection
/// type; this helper parses bytes explicitly so every failure shares the
/// one categorized envelope.
///
/// # Errors
/// `Input` (`invalid_json`) when the body is not valid JSON for `T`.
pub fn parse_json<T: DeserializeOwned>(bytes: &[u8]) -> Result<T, JoinError> {
    serde_json::from_slice(bytes)
        .map_err(|e| JoinError::input("invalid_json", format!("malformed request body: {e}")))
}
