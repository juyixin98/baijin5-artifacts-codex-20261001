//! Axum interop service exposing the codec over HTTP.
//!
//! Endpoints:
//!   GET  /healthz        liveness probe
//!   GET  /version        crate / format / toolchain versions
//!   POST /v1/encode      {"values": [u64, ...]}  -> {"hex": "...", "stats": {...}}
//!   POST /v1/decode      {"hex": "..."}          -> {"values": [...], "stats": {...}}
//!
//! Every response (success or failure) carries a `run_id` so service logs
//! can be correlated with the exact request. Failures map to explicit HTTP
//! statuses and a machine-readable error category — unknown states are
//! reported as 500/internal, never as success.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::{Deserialize, Serialize};
use tower_http::limit::RequestBodyLimitLayer;
use tower_http::trace::TraceLayer;

use crate::config::AppConfig;
use crate::decode::{self, DecodeStats};
use crate::encode::{self, EncodeStats};
use crate::error::{CodecError, ErrorCategory};
use crate::format::FORMAT_VERSION;

/// Shared service state.
#[derive(Clone)]
pub struct AppState {
    pub cfg: Arc<AppConfig>,
    pub boot_id: String,
    pub request_seq: Arc<AtomicU64>,
}

impl AppState {
    pub fn new(cfg: AppConfig) -> Self {
        let boot_id = format!(
            "boot-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_millis())
                .unwrap_or(0)
        );
        AppState {
            cfg: Arc::new(cfg),
            boot_id,
            request_seq: Arc::new(AtomicU64::new(0)),
        }
    }

    fn next_run_id(&self) -> String {
        let n = self.request_seq.fetch_add(1, Ordering::Relaxed);
        format!("{}-req-{:06}", self.boot_id, n)
    }
}

/// Build the application router (also used by integration tests).
pub fn build_router(state: AppState) -> Router {
    let body_limit = state.cfg.max_http_body_bytes;
    Router::new()
        .route("/healthz", get(healthz))
        .route("/version", get(version))
        .route("/v1/encode", post(encode_handler))
        .route("/v1/decode", post(decode_handler))
        .layer(RequestBodyLimitLayer::new(body_limit))
        .layer(TraceLayer::new_for_http())
        .with_state(state)
}

async fn healthz() -> Json<serde_json::Value> {
    Json(serde_json::json!({"status": "ok"}))
}

#[derive(Serialize)]
struct VersionResponse {
    crate_version: &'static str,
    format_version: u8,
    rustc: &'static str,
}

async fn version() -> Json<VersionResponse> {
    Json(VersionResponse {
        crate_version: env!("CARGO_PKG_VERSION"),
        format_version: FORMAT_VERSION,
        rustc: env!("BUILD_RUSTC_VERSION"),
    })
}

#[derive(Deserialize)]
pub struct EncodeRequest {
    pub values: Vec<u64>,
}

#[derive(Serialize)]
pub struct EncodeResponse {
    pub run_id: String,
    pub hex: String,
    pub stats: EncodeStats,
}

#[derive(Deserialize)]
pub struct DecodeRequest {
    pub hex: String,
}

#[derive(Serialize)]
pub struct DecodeResponse {
    pub run_id: String,
    pub values: Vec<u64>,
    pub stats: DecodeStats,
}

#[derive(Serialize)]
pub struct ErrorBody {
    pub run_id: String,
    pub error: ErrorDetail,
}

#[derive(Serialize)]
pub struct ErrorDetail {
    pub category: ErrorCategory,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub block_index: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub offset: Option<usize>,
}

fn error_response(status: StatusCode, run_id: &str, err: &CodecError) -> axum::response::Response {
    tracing::warn!(
        run_id,
        category = ?err.category,
        block_index = ?err.block_index,
        offset = ?err.offset,
        "request rejected: {}",
        err.message
    );
    (
        status,
        Json(ErrorBody {
            run_id: run_id.to_string(),
            error: ErrorDetail {
                category: err.category,
                message: err.message.clone(),
                block_index: err.block_index,
                offset: err.offset,
            },
        }),
    )
        .into_response()
}

async fn encode_handler(
    State(state): State<AppState>,
    Json(req): Json<EncodeRequest>,
) -> axum::response::Response {
    let run_id = state.next_run_id();
    tracing::info!(run_id, input_values = req.values.len(), "encode request");

    if req.values.len() > state.cfg.max_encode_values {
        let err = CodecError::budget_exceeded(
            "encode-values",
            state.cfg.max_encode_values,
            req.values.len(),
        );
        return error_response(StatusCode::PAYLOAD_TOO_LARGE, &run_id, &err);
    }

    match encode::encode_column_with_stats(&req.values, &state.cfg.encoder) {
        Ok((bytes, stats)) => {
            tracing::info!(
                run_id,
                blocks = stats.blocks,
                rle = stats.rle_blocks,
                bitpack = stats.bitpack_blocks,
                out_bytes = stats.output_bytes,
                "encode ok"
            );
            (
                StatusCode::OK,
                Json(EncodeResponse {
                    run_id,
                    hex: hex::encode(bytes),
                    stats,
                }),
            )
                .into_response()
        }
        Err(err) => error_response(StatusCode::UNPROCESSABLE_ENTITY, &run_id, &err),
    }
}

async fn decode_handler(
    State(state): State<AppState>,
    Json(req): Json<DecodeRequest>,
) -> axum::response::Response {
    let run_id = state.next_run_id();
    tracing::info!(run_id, hex_len = req.hex.len(), "decode request");

    let bytes = match hex::decode(req.hex.trim()) {
        Ok(b) => b,
        Err(e) => {
            let err = CodecError::new(
                ErrorCategory::TruncatedBody,
                format!("request body is not valid hex: {e}"),
            );
            return error_response(StatusCode::BAD_REQUEST, &run_id, &err);
        }
    };

    match decode::decode_column(&bytes, &state.cfg.budget) {
        Ok((values, stats)) => {
            tracing::info!(
                run_id,
                blocks = stats.blocks,
                decoded = stats.decoded_values,
                consumed = stats.consumed_bytes,
                "decode ok"
            );
            (
                StatusCode::OK,
                Json(DecodeResponse {
                    run_id,
                    values,
                    stats,
                }),
            )
                .into_response()
        }
        Err(err) => error_response(StatusCode::UNPROCESSABLE_ENTITY, &run_id, &err),
    }
}
