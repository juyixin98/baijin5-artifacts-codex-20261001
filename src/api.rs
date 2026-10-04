//! HTTP API (axum). Thin layer: all decisions are logged via
//! `diagnostics::DecisionRecord`, all heavy lifting lives in the
//! chunker/format/recovery modules.

use crate::chunker::{chunk_buffer, ChunkParams};
use crate::config::ServiceConfig;
use crate::diagnostics::{digest_prefix, new_request_id, Decision, DecisionRecord};
use crate::error::{CdcError, ErrorCategory};
use crate::format;
use crate::limits::Limits;
use crate::manifest::Manifest;
use crate::recovery;
use axum::{
    body::Bytes,
    extract::State,
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::sync::Arc;

pub struct AppState {
    pub params: ChunkParams,
    pub limits: Limits,
    pub manifest: Manifest,
}

pub fn router(config: ServiceConfig) -> Router {
    let state = Arc::new(AppState {
        params: config.chunker,
        limits: config.limits,
        manifest: Manifest::for_params(config.chunker),
    });
    Router::new()
        .route("/v1/health", get(health))
        .route("/v1/manifest", get(get_manifest))
        .route("/v1/chunk", post(chunk_json))
        .route("/v1/chunk-container", post(chunk_container))
        .route("/v1/verify", post(verify_container))
        .with_state(state)
}

#[derive(Serialize)]
struct ErrorBody {
    request_id: String,
    error: ErrorDetail,
}

#[derive(Serialize)]
struct ErrorDetail {
    category: ErrorCategory,
    message: String,
}

fn status_for(category: ErrorCategory) -> StatusCode {
    match category {
        ErrorCategory::InvalidParams => StatusCode::INTERNAL_SERVER_ERROR,
        ErrorCategory::LimitExceeded => StatusCode::PAYLOAD_TOO_LARGE,
        ErrorCategory::MalformedContainer => StatusCode::BAD_REQUEST,
        ErrorCategory::DigestMismatch | ErrorCategory::PayloadMismatch => {
            StatusCode::UNPROCESSABLE_ENTITY
        }
        ErrorCategory::Undetermined => StatusCode::UNPROCESSABLE_ENTITY,
    }
}

fn error_response(request_id: &str, err: &CdcError) -> Response {
    (
        status_for(err.category),
        Json(ErrorBody {
            request_id: request_id.to_string(),
            error: ErrorDetail {
                category: err.category,
                message: err.message.clone(),
            },
        }),
    )
        .into_response()
}

fn request_id_from(headers: &HeaderMap) -> String {
    headers
        .get("x-request-id")
        .and_then(|v| v.to_str().ok())
        .filter(|s| !s.is_empty() && s.len() <= 128)
        .map(|s| s.to_string())
        .unwrap_or_else(new_request_id)
}

async fn health() -> Json<serde_json::Value> {
    Json(serde_json::json!({ "status": "ok" }))
}

async fn get_manifest(State(state): State<Arc<AppState>>) -> Json<Manifest> {
    Json(state.manifest.clone())
}

#[derive(Serialize)]
struct ChunkEntry {
    offset: u64,
    len: u32,
    sha256: String,
}

#[derive(Serialize)]
struct ChunkResponse {
    request_id: String,
    manifest: Manifest,
    manifest_digest: String,
    payload_len: usize,
    payload_sha256: String,
    chunks: Vec<ChunkEntry>,
}

async fn chunk_json(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let request_id = request_id_from(&headers);
    match do_chunk(&state, &body) {
        Ok((chunks, payload_sha256)) => {
            let entries: Vec<ChunkEntry> = chunks
                .iter()
                .map(|c| ChunkEntry {
                    offset: c.offset,
                    len: c.len,
                    sha256: hex::encode(Sha256::digest(
                        &body[c.offset as usize..(c.offset + c.len as u64) as usize],
                    )),
                })
                .collect();
            DecisionRecord {
                request_id: request_id.clone(),
                operation: "chunk_json".into(),
                decision: Decision::Accept,
                reason: "chunked within limits".into(),
                input_bytes: Some(body.len()),
                chunk_count: Some(entries.len()),
                payload_digest_prefix: Some(digest_prefix(&payload_sha256)),
            }
            .emit();
            Json(ChunkResponse {
                request_id,
                manifest: state.manifest.clone(),
                manifest_digest: state.manifest.digest_hex(),
                payload_len: body.len(),
                payload_sha256,
                chunks: entries,
            })
            .into_response()
        }
        Err(err) => {
            DecisionRecord {
                request_id: request_id.clone(),
                operation: "chunk_json".into(),
                decision: Decision::Reject,
                reason: err.message.clone(),
                input_bytes: Some(body.len()),
                chunk_count: None,
                payload_digest_prefix: None,
            }
            .emit();
            error_response(&request_id, &err)
        }
    }
}

fn do_chunk(state: &AppState, body: &[u8]) -> Result<(Vec<crate::chunker::ChunkRef>, String), CdcError> {
    state.limits.check_body(body.len())?;
    state.limits.check_input(body.len())?;
    let chunks = chunk_buffer(state.params, body)?;
    state.limits.check_chunk_count(chunks.len())?;
    Ok((chunks, hex::encode(Sha256::digest(body))))
}

async fn chunk_container(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let request_id = request_id_from(&headers);
    match do_chunk(&state, &body) {
        Ok((chunks, payload_sha256)) => {
            let encoded = format::encode(&state.manifest, &chunks, &body);
            DecisionRecord {
                request_id: request_id.clone(),
                operation: "chunk_container".into(),
                decision: Decision::Accept,
                reason: "container encoded".into(),
                input_bytes: Some(body.len()),
                chunk_count: Some(chunks.len()),
                payload_digest_prefix: Some(digest_prefix(&payload_sha256)),
            }
            .emit();
            (
                StatusCode::OK,
                [
                    ("content-type", "application/x-cdcb"),
                    ("x-request-id", request_id.as_str()),
                ],
                encoded,
            )
                .into_response()
        }
        Err(err) => {
            DecisionRecord {
                request_id: request_id.clone(),
                operation: "chunk_container".into(),
                decision: Decision::Reject,
                reason: err.message.clone(),
                input_bytes: Some(body.len()),
                chunk_count: None,
                payload_digest_prefix: None,
            }
            .emit();
            error_response(&request_id, &err)
        }
    }
}

#[derive(Serialize)]
struct VerifyResponse {
    request_id: String,
    decision: Decision,
    reason: String,
    manifest: Manifest,
    chunk_count: usize,
    payload_len: usize,
    payload_sha256: String,
}

async fn verify_container(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let request_id = request_id_from(&headers);
    if let Err(err) = state.limits.check_body(body.len()) {
        DecisionRecord {
            request_id: request_id.clone(),
            operation: "verify".into(),
            decision: Decision::Reject,
            reason: err.message.clone(),
            input_bytes: Some(body.len()),
            chunk_count: None,
            payload_digest_prefix: None,
        }
        .emit();
        return error_response(&request_id, &err);
    }
    match recovery::recover(&body, None) {
        Ok(rec) => {
            let payload_sha256 = hex::encode(Sha256::digest(&rec.payload));
            let compatible = rec.manifest.compatible_with(&state.manifest);
            let (decision, reason) = if compatible {
                (Decision::Accept, "container verified; manifest matches service regime")
            } else {
                (
                    Decision::Undetermined,
                    "container verified internally, but its manifest differs from this service's regime; boundaries not comparable",
                )
            };
            DecisionRecord {
                request_id: request_id.clone(),
                operation: "verify".into(),
                decision,
                reason: reason.into(),
                input_bytes: Some(body.len()),
                chunk_count: Some(rec.chunks.len()),
                payload_digest_prefix: Some(digest_prefix(&payload_sha256)),
            }
            .emit();
            Json(VerifyResponse {
                request_id,
                decision,
                reason: reason.into(),
                manifest: rec.manifest,
                chunk_count: rec.chunks.len(),
                payload_len: rec.payload.len(),
                payload_sha256,
            })
            .into_response()
        }
        Err(err) => {
            DecisionRecord {
                request_id: request_id.clone(),
                operation: "verify".into(),
                decision: Decision::Reject,
                reason: err.message.clone(),
                input_bytes: Some(body.len()),
                chunk_count: None,
                payload_digest_prefix: None,
            }
            .emit();
            error_response(&request_id, &err)
        }
    }
}
