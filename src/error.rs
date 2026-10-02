//! Error taxonomy.
//!
//! Every failure in the service is classified into exactly one of four
//! categories so that callers (and test logs) can distinguish:
//! - `Input`             — the request itself is malformed (bad page index,
//!                         offset overflow, empty batch, bad base64, ...).
//! - `StateConflict`     — the request is well-formed but conflicts with
//!                         current state (unknown snapshot, refcount anomaly).
//! - `ResourceExhausted` — the physical page capacity is exhausted.
//! - `ComputeFailure`    — the operation itself failed (IO, corrupt page
//!                         file, manifest serialization).

use axum::{
    Json,
    http::StatusCode,
    response::{IntoResponse, Response},
};
use serde::Serialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCategory {
    Input,
    StateConflict,
    ResourceExhausted,
    ComputeFailure,
}

#[derive(Debug, thiserror::Error)]
pub enum AppError {
    /// Malformed request; retrying unchanged will fail again.
    #[error("{0}")]
    Input(String),
    /// Well-formed request conflicting with current state.
    #[error("{0}")]
    State(String),
    /// Physical page capacity exhausted.
    #[error("{0}")]
    Resource(String),
    /// The computation/IO itself failed.
    #[error("{0}")]
    Compute(String),
}

#[derive(Debug, Serialize)]
pub struct ErrorBody {
    pub error: ErrorDetail,
}

#[derive(Debug, Serialize)]
pub struct ErrorDetail {
    pub category: ErrorCategory,
    pub code: &'static str,
    pub message: String,
    /// Run id of the request that produced this error; correlates with
    /// server logs for replay.
    pub run_id: String,
}

impl AppError {
    pub fn category(&self) -> ErrorCategory {
        match self {
            AppError::Input(_) => ErrorCategory::Input,
            AppError::State(_) => ErrorCategory::StateConflict,
            AppError::Resource(_) => ErrorCategory::ResourceExhausted,
            AppError::Compute(_) => ErrorCategory::ComputeFailure,
        }
    }

    pub fn code(&self) -> &'static str {
        match self {
            AppError::Input(_) => "input_invalid",
            AppError::State(_) => "state_conflict",
            AppError::Resource(_) => "capacity_exhausted",
            AppError::Compute(_) => "compute_failure",
        }
    }

    pub fn status(&self) -> StatusCode {
        match self {
            AppError::Input(_) => StatusCode::BAD_REQUEST,
            AppError::State(_) => StatusCode::CONFLICT,
            AppError::Resource(_) => StatusCode::INSUFFICIENT_STORAGE,
            AppError::Compute(_) => StatusCode::INTERNAL_SERVER_ERROR,
        }
    }

    /// Render as an HTTP response carrying the request's run id.
    pub fn into_response_with(self, run_id: &str) -> Response {
        let status = self.status();
        let body = ErrorBody {
            error: ErrorDetail {
                category: self.category(),
                code: self.code(),
                message: self.to_string(),
                run_id: run_id.to_string(),
            },
        };
        (status, Json(body)).into_response()
    }
}

impl From<std::io::Error> for AppError {
    fn from(e: std::io::Error) -> Self {
        AppError::Compute(format!("io error: {e}"))
    }
}
