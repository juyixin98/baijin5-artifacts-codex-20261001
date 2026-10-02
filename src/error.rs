//! API error taxonomy. Every error maps to a stable machine-readable `code`
//! so clients and tests can assert on failure *categories*, not messages.

use axum::{
    http::StatusCode,
    response::{IntoResponse, Response},
    Json,
};
use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct ErrorBody {
    pub error: ErrorDetail,
}

#[derive(Debug, Serialize)]
pub struct ErrorDetail {
    pub code: &'static str,
    pub message: String,
}

#[derive(Debug)]
pub enum ApiError {
    /// 400 — malformed input (bad sector range, zero length, duplicate id, ...).
    Validation(String),
    /// 404 — referenced run or request does not exist.
    NotFound(String),
    /// 409 — request state conflicts with the operation (e.g. already finished).
    Conflict(String),
    /// 500 — unexpected internal failure (io, serialization, ...).
    Internal(String),
}

impl ApiError {
    fn parts(&self) -> (StatusCode, &'static str, &str) {
        match self {
            ApiError::Validation(m) => (StatusCode::BAD_REQUEST, "VALIDATION_FAILED", m),
            ApiError::NotFound(m) => (StatusCode::NOT_FOUND, "NOT_FOUND", m),
            ApiError::Conflict(m) => (StatusCode::CONFLICT, "CONFLICT", m),
            ApiError::Internal(m) => (StatusCode::INTERNAL_SERVER_ERROR, "INTERNAL", m),
        }
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let (status, code, message) = self.parts();
        let body = ErrorBody {
            error: ErrorDetail {
                code,
                message: message.to_string(),
            },
        };
        (status, Json(body)).into_response()
    }
}

impl From<std::io::Error> for ApiError {
    fn from(e: std::io::Error) -> Self {
        ApiError::Internal(format!("io error: {e}"))
    }
}

impl From<serde_json::Error> for ApiError {
    fn from(e: serde_json::Error) -> Self {
        ApiError::Internal(format!("json error: {e}"))
    }
}
