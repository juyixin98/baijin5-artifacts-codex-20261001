//! HTTP API: Axum router, handlers, request-id middleware, error mapping.
//!
//! Endpoints:
//! - `GET  /healthz`              liveness
//! - `GET  /v1/params`            algorithm spec + active resource limits
//! - `POST /v1/chunk`             raw bytes -> manifest (JSON, or binary with
//!   `?format=binary`)
//! - `POST /v1/manifest/parse`    binary "CDCM" manifest -> JSON manifest
//! - `POST /v1/verify`            {manifest, content_base64, reference_base64?}
//!   -> verification report
//!
//! Every request gets a `req-N` id (response header `x-request-id`) and ends
//! in a structured decision record (see `diagnostics`).

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use axum::{
    body::Bytes,
    extract::{rejection::BytesRejection, DefaultBodyLimit, Query, Request, State},
    http::{header, HeaderValue, StatusCode},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post},
    Extension, Json, Router,
};
use base64::Engine;
use serde::{Deserialize, Serialize};
use tower::limit::ConcurrencyLimitLayer;

use crate::chunker::chunk_all;
use crate::diagnostics::{self, DecisionKind};
use crate::format;
use crate::limits::Limits;
use crate::manifest::Manifest;
use crate::params::{AlgorithmSpec, ChunkParams};
use crate::recovery;

pub struct AppState {
    pub params: ChunkParams,
    pub limits: Limits,
}

impl AppState {
    pub fn new(params: ChunkParams, limits: Limits) -> Self {
        AppState { params, limits }
    }
}

#[derive(Debug, Clone)]
pub struct RequestId(pub String);

pub fn build_router(state: Arc<AppState>) -> Router {
    Router::new()
        .route("/healthz", get(healthz))
        .route("/v1/params", get(get_params))
        .route("/v1/chunk", post(chunk_handler))
        .route("/v1/manifest/parse", post(parse_manifest_handler))
        .route("/v1/verify", post(verify_handler))
        .layer(DefaultBodyLimit::max(state.limits.max_body_bytes))
        .layer(middleware::from_fn_with_state(
            state.clone(),
            request_middleware,
        ))
        .layer(ConcurrencyLimitLayer::new(state.limits.max_concurrent))
        .with_state(state)
}

/// Assigns a request id, applies the per-request timeout, and records a
/// decision when a request is rejected by resource control.
async fn request_middleware(
    State(state): State<Arc<AppState>>,
    mut req: Request,
    next: Next,
) -> Response {
    static NEXT_ID: AtomicU64 = AtomicU64::new(1);
    let id = format!("req-{}", NEXT_ID.fetch_add(1, Ordering::Relaxed));
    req.extensions_mut().insert(RequestId(id.clone()));

    match tokio::time::timeout(state.limits.request_timeout, next.run(req)).await {
        Ok(mut resp) => {
            if let Ok(v) = HeaderValue::from_str(&id) {
                resp.headers_mut().insert("x-request-id", v);
            }
            resp
        }
        Err(_) => {
            diagnostics::record(
                &id,
                "request",
                DecisionKind::Reject,
                "request exceeded processing timeout",
                diagnostics::state_of(&[(
                    "timeout_secs",
                    state.limits.request_timeout.as_secs().to_string(),
                )]),
            );
            (
                StatusCode::REQUEST_TIMEOUT,
                Json(serde_json::json!({
                    "error": { "category": "timeout", "message": "request processing timeout" },
                    "request_id": id,
                })),
            )
                .into_response()
        }
    }
}

fn error_response(
    req_id: &RequestId,
    status: StatusCode,
    category: &str,
    message: String,
) -> Response {
    (
        status,
        Json(serde_json::json!({
            "error": { "category": category, "message": message },
            "request_id": req_id.0,
        })),
    )
        .into_response()
}

async fn healthz() -> impl IntoResponse {
    Json(serde_json::json!({ "status": "ok" }))
}

#[derive(Serialize)]
struct ParamsResponse {
    algorithm: AlgorithmSpec,
    limits: Limits,
}

async fn get_params(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    Json(ParamsResponse {
        algorithm: AlgorithmSpec::from_params(&state.params),
        limits: state.limits,
    })
}

#[derive(Debug, Deserialize)]
struct ChunkQuery {
    format: Option<String>,
}

async fn chunk_handler(
    State(state): State<Arc<AppState>>,
    Extension(req_id): Extension<RequestId>,
    Query(query): Query<ChunkQuery>,
    body: Result<Bytes, BytesRejection>,
) -> Response {
    let bytes = match body {
        Ok(b) => b,
        Err(rej) => {
            let msg = rej.body_text();
            diagnostics::record(
                &req_id.0,
                "chunk",
                DecisionKind::Reject,
                "request body rejected by resource control",
                diagnostics::state_of(&[("limit", format!("max_body_bytes={}", state.limits.max_body_bytes))]),
            );
            return error_response(
                &req_id,
                StatusCode::PAYLOAD_TOO_LARGE,
                "body_too_large",
                msg,
            );
        }
    };

    let outcome = chunk_all(state.params, &bytes);
    if outcome.chunks.len() > state.limits.max_chunks {
        diagnostics::record(
            &req_id.0,
            "chunk",
            DecisionKind::Reject,
            "chunk count exceeds limit",
            diagnostics::state_of(&[
                ("chunks", outcome.chunks.len().to_string()),
                ("limit", format!("max_chunks={}", state.limits.max_chunks)),
            ]),
        );
        return error_response(
            &req_id,
            StatusCode::PAYLOAD_TOO_LARGE,
            "too_many_chunks",
            format!(
                "input produced {} chunks, limit is {}",
                outcome.chunks.len(),
                state.limits.max_chunks
            ),
        );
    }
    let manifest = Manifest::from_outcome(&state.params, outcome);

    diagnostics::record(
        &req_id.0,
        "chunk",
        DecisionKind::Accept,
        "input chunked",
        diagnostics::state_of(&[
            ("total_len", manifest.total_len.to_string()),
            ("chunks", manifest.chunks.len().to_string()),
            ("content_sha256", diagnostics::short_digest(&manifest.content_sha256)),
        ]),
    );

    if query.format.as_deref() == Some("binary") {
        let encoded = format::encode(&manifest);
        (
            StatusCode::OK,
            [(header::CONTENT_TYPE, "application/x-cdc-manifest")],
            encoded,
        )
            .into_response()
    } else {
        Json(manifest).into_response()
    }
}

async fn parse_manifest_handler(
    Extension(req_id): Extension<RequestId>,
    body: Result<Bytes, BytesRejection>,
) -> Response {
    let bytes = match body {
        Ok(b) => b,
        Err(rej) => {
            return error_response(
                &req_id,
                StatusCode::PAYLOAD_TOO_LARGE,
                "body_too_large",
                rej.body_text(),
            )
        }
    };
    match format::decode(&bytes) {
        Ok(manifest) => {
            diagnostics::record(
                &req_id.0,
                "manifest/parse",
                DecisionKind::Accept,
                "binary manifest decoded",
                diagnostics::state_of(&[
                    ("total_len", manifest.total_len.to_string()),
                    ("chunks", manifest.chunks.len().to_string()),
                ]),
            );
            Json(manifest).into_response()
        }
        Err(e) => {
            diagnostics::record(
                &req_id.0,
                "manifest/parse",
                DecisionKind::Undetermined,
                "binary manifest not decodable",
                diagnostics::state_of(&[("cause", e.to_string())]),
            );
            error_response(&req_id, StatusCode::BAD_REQUEST, "malformed_manifest", e.to_string())
        }
    }
}

#[derive(Debug, Deserialize)]
struct VerifyRequest {
    manifest: Manifest,
    /// Base64 of the concatenated chunk bytes to check against the manifest.
    content_base64: String,
    /// Optional base64 reference: when present, the reassembled bytes are
    /// also compared byte-for-byte against it (the authoritative check).
    reference_base64: Option<String>,
}

#[derive(Serialize)]
struct VerifyResponse {
    decision: &'static str,
    total_len: u64,
    chunks_verified: usize,
    /// Present only when a reference was supplied.
    byte_compare: Option<&'static str>,
    request_id: String,
}

async fn verify_handler(
    State(state): State<Arc<AppState>>,
    Extension(req_id): Extension<RequestId>,
    body: Result<Json<VerifyRequest>, axum::extract::rejection::JsonRejection>,
) -> Response {
    let Json(req) = match body {
        Ok(j) => j,
        Err(rej) => {
            diagnostics::record(
                &req_id.0,
                "verify",
                DecisionKind::Reject,
                "request body is not valid verify JSON",
                diagnostics::state_of(&[("cause", rej.body_text())]),
            );
            return error_response(&req_id, StatusCode::BAD_REQUEST, "invalid_request", rej.body_text());
        }
    };

    let b64 = base64::engine::general_purpose::STANDARD;
    let content = match b64.decode(&req.content_base64) {
        Ok(c) => c,
        Err(e) => {
            return verify_reject(&req_id, "invalid_base64", format!("content_base64: {e}"));
        }
    };
    let reference = match &req.reference_base64 {
        Some(r) => match b64.decode(r) {
            Ok(c) => Some(c),
            Err(e) => {
                return verify_reject(&req_id, "invalid_base64", format!("reference_base64: {e}"));
            }
        },
        None => None,
    };

    if req.manifest.chunks.len() > state.limits.max_chunks {
        return verify_reject(&req_id, "too_many_chunks", "manifest exceeds chunk limit".into());
    }

    let result = match &reference {
        Some(reference) => recovery::verify_against(&req.manifest, &content, reference),
        None => recovery::reassemble(&req.manifest, &content).map(|_| ()),
    };

    match result {
        Ok(()) => {
            diagnostics::record(
                &req_id.0,
                "verify",
                DecisionKind::Accept,
                "manifest verified against supplied bytes",
                diagnostics::state_of(&[
                    ("total_len", req.manifest.total_len.to_string()),
                    ("chunks", req.manifest.chunks.len().to_string()),
                    ("byte_compare", reference.is_some().to_string()),
                ]),
            );
            Json(VerifyResponse {
                decision: "accept",
                total_len: req.manifest.total_len,
                chunks_verified: req.manifest.chunks.len(),
                byte_compare: reference.as_ref().map(|_| "identical"),
                request_id: req_id.0.clone(),
            })
            .into_response()
        }
        Err(e) => {
            diagnostics::record(
                &req_id.0,
                "verify",
                DecisionKind::Undetermined,
                "verification failed",
                diagnostics::state_of(&[
                    ("category", e.category().to_string()),
                    ("cause", e.to_string()),
                ]),
            );
            error_response(&req_id, StatusCode::UNPROCESSABLE_ENTITY, e.category(), e.to_string())
        }
    }
}

fn verify_reject(req_id: &RequestId, category: &str, message: String) -> Response {
    diagnostics::record(
        &req_id.0,
        "verify",
        DecisionKind::Reject,
        "verify request rejected",
        diagnostics::state_of(&[("category", category.to_string())]),
    );
    let status = match category {
        "too_many_chunks" => StatusCode::PAYLOAD_TOO_LARGE,
        _ => StatusCode::BAD_REQUEST,
    };
    error_response(req_id, status, category, message)
}
