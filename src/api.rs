//! Axum HTTP service exposing the codec.
//!
//! Endpoints:
//! - `GET  /health`     -> liveness
//! - `GET  /version`    -> crate / format versions
//! - `POST /v1/encode`  -> {"values": [u64, ...]}  => {"blocks_b64": [...], ...}
//! - `POST /v1/decode`  -> {"blocks_b64": [...]}   => {"values": [...]}
//!
//! Codec failures map to 422 with a structured error body; budget violations
//! map to 413. Unknown or exceptional states are never reported as success.

use axum::{
    extract::State,
    http::StatusCode,
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use base64::{engine::general_purpose::STANDARD as B64, Engine};
use serde::{Deserialize, Serialize};

use crate::budget::DecodeBudget;
use crate::decode;
use crate::encode::{self, EncodeOptions};
use crate::error::{Error, ErrorCategory};

/// Shared service state.
#[derive(Debug, Clone)]
pub struct AppState {
    pub budget: DecodeBudget,
    pub encode_options: EncodeOptions,
}

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/version", get(version))
        .route("/v1/encode", post(encode_handler))
        .route("/v1/decode", post(decode_handler))
        .with_state(state)
}

async fn health() -> Json<serde_json::Value> {
    Json(serde_json::json!({"status": "ok"}))
}

async fn version() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "crate": env!("CARGO_PKG_NAME"),
        "crate_version": env!("CARGO_PKG_VERSION"),
        "format_version": crate::format::VERSION,
    }))
}

#[derive(Debug, Deserialize)]
pub struct EncodeRequest {
    pub values: Vec<u64>,
}

#[derive(Debug, Serialize)]
pub struct EncodeResponse {
    pub block_count: usize,
    pub input_values: usize,
    pub output_bytes: usize,
    /// The whole encoded column (concatenated blocks), base64.
    pub column_b64: String,
}

async fn encode_handler(
    State(state): State<AppState>,
    Json(req): Json<EncodeRequest>,
) -> Result<Json<EncodeResponse>, ApiError> {
    if req.values.len() as u64 > state.budget.max_total_values {
        return Err(ApiError::from(Error::new(
            ErrorCategory::ValueCountOverBudget,
            0,
            format!(
                "encode request holds {} values, budget allows {}",
                req.values.len(),
                state.budget.max_total_values
            ),
        )));
    }
    let bytes = encode::encode_column(&req.values, &state.encode_options);
    tracing::info!(
        input_values = req.values.len(),
        output_bytes = bytes.len(),
        "encoded column"
    );
    Ok(Json(EncodeResponse {
        block_count: count_blocks(&bytes),
        input_values: req.values.len(),
        output_bytes: bytes.len(),
        column_b64: B64.encode(&bytes),
    }))
}

#[derive(Debug, Deserialize)]
pub struct DecodeRequest {
    pub column_b64: String,
}

#[derive(Debug, Serialize)]
pub struct DecodeResponse {
    pub value_count: usize,
    pub values: Vec<u64>,
}

async fn decode_handler(
    State(state): State<AppState>,
    Json(req): Json<DecodeRequest>,
) -> Result<Json<DecodeResponse>, ApiError> {
    let bytes = B64.decode(req.column_b64.as_bytes()).map_err(|e| {
        ApiError::from(Error::new(
            ErrorCategory::BadMagic,
            0,
            format!("request body is not valid base64: {}", e),
        ))
    })?;
    let values = decode::decode_column(&bytes, &state.budget).map_err(ApiError::from)?;
    tracing::info!(value_count = values.len(), "decoded column");
    Ok(Json(DecodeResponse {
        value_count: values.len(),
        values,
    }))
}

/// Count blocks by walking headers with a strict budget; used for stats only.
fn count_blocks(bytes: &[u8]) -> usize {
    let mut count = 0;
    let mut offset = 0;
    let budget = DecodeBudget {
        max_values_per_block: u64::MAX,
        max_payload_bytes: u64::MAX,
        max_runs_per_block: u64::MAX,
        max_total_values: u64::MAX,
    };
    while offset < bytes.len() {
        match decode::decode_block(&bytes[offset..], &budget) {
            Ok((_, consumed)) => {
                count += 1;
                offset += consumed;
            }
            Err(_) => break,
        }
    }
    count
}

/// HTTP-facing error: structured body, correct status class.
pub struct ApiError {
    status: StatusCode,
    body: serde_json::Value,
}

impl From<Error> for ApiError {
    fn from(err: Error) -> Self {
        let status = match err.category {
            ErrorCategory::ValueCountOverBudget
            | ErrorCategory::PayloadLenOverBudget
            | ErrorCategory::RunCountOverBudget => StatusCode::PAYLOAD_TOO_LARGE,
            _ => StatusCode::UNPROCESSABLE_ENTITY,
        };
        ApiError {
            status,
            body: serde_json::json!({
                "error": {
                    "category": err.category.as_str(),
                    "offset": err.offset,
                    "message": err.detail,
                }
            }),
        }
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> axum::response::Response {
        (self.status, Json(self.body)).into_response()
    }
}
