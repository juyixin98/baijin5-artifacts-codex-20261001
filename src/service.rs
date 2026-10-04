//! Axum HTTP boundary.
//!
//! Endpoints (all JSON; tables travel as base64 of the binary format):
//!
//! - `GET  /health`       — liveness.
//! - `POST /v1/encode`    — `{keys, params?}` → `{run_id, params, stats, table_b64}`.
//! - `POST /v1/subtract`  — `{table_a_b64, table_b_b64}` → difference table.
//! - `POST /v1/decode`    — `{table_a_b64, table_b_b64}` → `{run_id, complete, only_a, only_b, stats}`.
//!
//! Error contract: every failure is
//! `{run_id, error: {category, code, message, detail?}}` where category is
//! one of `invalid_input | state_conflict | resource_exhausted |
//! computation_failed`. A decode that cannot finish is a 422
//! `decode_incomplete` and never carries partial result sets.

use std::collections::HashSet;
use std::sync::Arc;

use axum::{
    extract::{DefaultBodyLimit, FromRequest, Request, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use base64::{engine::general_purpose::STANDARD as B64, Engine};
use serde::{Deserialize, Serialize};
use tracing::{info, warn};

use crate::error::{ErrorCategory, IbltError};
use crate::format;
use crate::iblt::{Iblt, Params};
use crate::limits::Limits;
use crate::runlog::RunIds;

#[derive(Clone)]
pub struct AppState {
    limits: Limits,
    run_ids: Arc<RunIds>,
}

pub fn app(limits: Limits) -> Router {
    let state = AppState {
        limits,
        run_ids: Arc::new(RunIds::new()),
    };
    Router::new()
        .route("/health", get(health))
        .route("/v1/encode", post(encode))
        .route("/v1/subtract", post(subtract_tables))
        .route("/v1/decode", post(decode))
        .layer(DefaultBodyLimit::max(limits.max_body_bytes))
        .with_state(state)
}

// ---------- request / response DTOs ----------

#[derive(Debug, Deserialize)]
pub struct EncodeRequest {
    pub keys: Vec<u64>,
    /// Optional table shape; defaults to `Params::for_set_size(keys.len())`.
    pub params: Option<Params>,
}

#[derive(Debug, Serialize)]
pub struct EncodeResponse {
    pub run_id: String,
    pub params: Params,
    pub stats: EncodeStats,
    pub table_b64: String,
}

#[derive(Debug, Serialize)]
pub struct EncodeStats {
    pub keys: usize,
    pub nonzero_cells: usize,
}

#[derive(Debug, Deserialize)]
pub struct TablesRequest {
    pub table_a_b64: String,
    pub table_b_b64: String,
}

#[derive(Debug, Serialize)]
pub struct SubtractResponse {
    pub run_id: String,
    pub params: Params,
    pub table_b64: String,
}

#[derive(Debug, Serialize)]
pub struct DecodeResponse {
    pub run_id: String,
    /// Always `true` here — incomplete decodes are errors, not partials.
    pub complete: bool,
    pub only_a: Vec<u64>,
    pub only_b: Vec<u64>,
    pub stats: DecodeStats,
}

#[derive(Debug, Serialize)]
pub struct DecodeStats {
    pub peeled: usize,
}

#[derive(Debug, Serialize)]
struct ErrorResponse<'a> {
    run_id: &'a str,
    error: ErrorBody,
}

#[derive(Debug, Serialize)]
struct ErrorBody {
    category: ErrorCategory,
    code: &'static str,
    message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    detail: Option<serde_json::Value>,
}

// ---------- handlers ----------

async fn health() -> Json<serde_json::Value> {
    Json(serde_json::json!({"status": "ok"}))
}

async fn encode(State(state): State<AppState>, req: Request) -> Response {
    let run_id = state.run_ids.next();
    let Json(req): Json<EncodeRequest> = match parse_json(req, &run_id).await {
        Ok(v) => v,
        Err(resp) => return resp,
    };
    info!(run_id, op = "encode", keys = req.keys.len(), "request received");

    run(req, &run_id, &state, |req, limits| {
        limits.check_key_count(req.keys.len())?;
        reject_duplicates(&req.keys)?;
        let params = req.params.unwrap_or_else(|| Params::for_set_size(req.keys.len()));
        limits.check_cell_count(params.cells as usize)?;
        let mut table = Iblt::new(params)?;
        for &key in &req.keys {
            table.insert(key);
        }
        let stats = EncodeStats {
            keys: req.keys.len(),
            nonzero_cells: table.nonzero_cells(),
        };
        info!(run_id, op = "encode", cells = params.cells, nonzero = stats.nonzero_cells, "encoded");
        Ok(Json(EncodeResponse {
            run_id: run_id.clone(),
            params,
            stats,
            table_b64: B64.encode(format::serialize(&table)),
        })
        .into_response())
    })
}

async fn subtract_tables(State(state): State<AppState>, req: Request) -> Response {
    let run_id = state.run_ids.next();
    let Json(req): Json<TablesRequest> = match parse_json(req, &run_id).await {
        Ok(v) => v,
        Err(resp) => return resp,
    };
    info!(run_id, op = "subtract", "request received");

    run(req, &run_id, &state, |req, limits| {
        let (a, b) = parse_pair(&req, limits)?;
        let diff = a.subtract(&b)?;
        info!(run_id, op = "subtract", nonzero = diff.nonzero_cells(), "subtracted");
        Ok(Json(SubtractResponse {
            run_id: run_id.clone(),
            params: diff.params(),
            table_b64: B64.encode(format::serialize(&diff)),
        })
        .into_response())
    })
}

async fn decode(State(state): State<AppState>, req: Request) -> Response {
    let run_id = state.run_ids.next();
    let Json(req): Json<TablesRequest> = match parse_json(req, &run_id).await {
        Ok(v) => v,
        Err(resp) => return resp,
    };
    info!(run_id, op = "decode", "request received");

    run(req, &run_id, &state, |req, limits| {
        let (a, b) = parse_pair(&req, limits)?;
        let diff = a.subtract(&b)?;
        let report = diff.decode().map_err(|e| {
            // Attach the actionable detail to an incomplete decode: the
            // caller must re-encode with more cells, so say so explicitly.
            if let IbltError::DecodeIncomplete { remaining_nonzero_cells, peeled } = e {
                warn!(
                    run_id, op = "decode", peeled, remaining_nonzero_cells,
                    decision = "refuse_partial_result",
                    "decode stalled; requesting larger table"
                );
                return IbltError::DecodeIncomplete { remaining_nonzero_cells, peeled };
            }
            e
        })?;
        info!(
            run_id, op = "decode", peeled = report.peeled,
            decision = "complete", "decode drained all cells"
        );
        Ok(Json(DecodeResponse {
            run_id: run_id.clone(),
            complete: true,
            only_a: report.only_a,
            only_b: report.only_b,
            stats: DecodeStats { peeled: report.peeled },
        })
        .into_response())
    })
}

// ---------- helpers ----------

/// Extract a JSON body, mapping rejections (malformed JSON, body too
/// large) into the service error contract.
// Err is a ready-made HTTP response; boxing it would only add indirection.
#[allow(clippy::result_large_err)]
async fn parse_json<T: serde::de::DeserializeOwned>(
    req: Request,
    run_id: &str,
) -> Result<Json<T>, Response> {
    match Json::<T>::from_request(req, &()).await {
        Ok(v) => Ok(v),
        Err(rejection) => {
            let err = if rejection.status() == StatusCode::PAYLOAD_TOO_LARGE {
                IbltError::ResourceExhausted(format!("request body too large: {rejection}"))
            } else {
                IbltError::InvalidInput(format!("malformed JSON body: {rejection}"))
            };
            Err(fail(run_id, err))
        }
    }
}

/// Run a handler body, converting any `IbltError` into the error contract.
fn run<Req, F>(req: Req, run_id: &str, state: &AppState, f: F) -> Response
where
    F: FnOnce(Req, &Limits) -> Result<Response, IbltError>,
{
    match f(req, &state.limits) {
        Ok(resp) => resp,
        Err(err) => fail(run_id, err),
    }
}

fn parse_pair(req: &TablesRequest, limits: &Limits) -> Result<(Iblt, Iblt), IbltError> {
    let a = parse_table(&req.table_a_b64, "table_a_b64", limits)?;
    let b = parse_table(&req.table_b_b64, "table_b_b64", limits)?;
    Ok((a, b))
}

fn parse_table(b64: &str, field: &str, limits: &Limits) -> Result<Iblt, IbltError> {
    let bytes = B64
        .decode(b64)
        .map_err(|e| IbltError::InvalidInput(format!("{field}: invalid base64: {e}")))?;
    format::parse(&bytes, limits).map_err(|e| match e {
        IbltError::InvalidInput(m) => IbltError::InvalidInput(format!("{field}: {m}")),
        other => other,
    })
}

fn reject_duplicates(keys: &[u64]) -> Result<(), IbltError> {
    let mut seen = HashSet::with_capacity(keys.len());
    let mut dups: Vec<u64> = keys
        .iter()
        .filter(|k| !seen.insert(**k))
        .copied()
        .collect();
    if dups.is_empty() {
        return Ok(());
    }
    dups.sort_unstable();
    dups.dedup();
    dups.truncate(10);
    Err(IbltError::InvalidInput(format!(
        "duplicate keys in one set are not encodable as a set: {dups:?}"
    )))
}

/// Build the uniform error response and log the decision.
fn fail(run_id: &str, err: IbltError) -> Response {
    let category = err.category();
    let status = match category {
        ErrorCategory::InvalidInput => StatusCode::BAD_REQUEST,
        ErrorCategory::StateConflict => StatusCode::CONFLICT,
        ErrorCategory::ResourceExhausted => StatusCode::UNPROCESSABLE_ENTITY,
        ErrorCategory::ComputationFailed => StatusCode::INTERNAL_SERVER_ERROR,
    };
    let detail = match &err {
        IbltError::DecodeIncomplete { remaining_nonzero_cells, peeled } => {
            Some(serde_json::json!({
                "remaining_nonzero_cells": remaining_nonzero_cells,
                "peeled": peeled,
                "hint": "table too small for this difference; re-encode both \
                         sides with more cells (roughly 1.5x the difference size) \
                         and retry",
            }))
        }
        _ => None,
    };
    warn!(
        run_id,
        category = ?category,
        code = err.code(),
        message = %err.message(),
        "request failed"
    );
    (
        status,
        Json(ErrorResponse {
            run_id,
            error: ErrorBody {
                category,
                code: err.code(),
                message: err.message(),
                detail,
            },
        }),
    )
        .into_response()
}
