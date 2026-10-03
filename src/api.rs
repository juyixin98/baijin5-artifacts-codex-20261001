//! Axum HTTP interface. Thin layer over [`Engine`]; all decisions and
//! diagnostics live in the engine.

use std::sync::{Arc, Mutex};

use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::{Deserialize, Serialize};

use crate::diag::Op;
use crate::engine::{Engine, Request};
use crate::hexutil;

pub type Shared = Arc<Mutex<Engine>>;

pub fn router(engine: Engine) -> Router {
    let shared: Shared = Arc::new(Mutex::new(engine));
    Router::new()
        .route("/healthz", get(healthz))
        .route("/v1/access", post(access))
        .route("/v1/resize", post(resize))
        .route("/v1/stats", get(stats))
        .route("/v1/stats/history", get(stats_history))
        .route("/v1/state", get(state))
        .route("/v1/diagnostics", get(diagnostics))
        .route("/v1/snapshot", post(snapshot))
        .route("/v1/restore", post(restore))
        .with_state(shared)
}

async fn healthz() -> &'static str {
    "ok"
}

#[derive(Debug, Deserialize)]
pub struct AccessRequest {
    request_id: Option<String>,
    op: String,
    page: u64,
    /// Hex-encoded payload; required for writes.
    data_hex: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct AccessResponse {
    request_id: String,
    seq: u64,
    status: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    outcome: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    data_hex: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<ErrorBody>,
}

#[derive(Debug, Serialize)]
pub struct ErrorBody {
    category: String,
    message: String,
}

fn outcome_name(o: crate::arc::Outcome) -> String {
    // serde rename_all snake_case keeps a single source of truth.
    serde_json::to_value(o)
        .ok()
        .and_then(|v| v.as_str().map(str::to_owned))
        .unwrap_or_else(|| format!("{o:?}"))
}

async fn access(
    State(shared): State<Shared>,
    Json(req): Json<AccessRequest>,
) -> impl IntoResponse {
    let op = match req.op.as_str() {
        "read" => Op::Read,
        "write" => Op::Write,
        other => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"error": {"category": "bad_request",
                    "message": format!("unknown op '{other}', expected read|write")}})),
            )
                .into_response();
        }
    };
    let data = match (&op, &req.data_hex) {
        (Op::Write, None) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"error": {"category": "bad_request",
                    "message": "write requires data_hex"}})),
            )
                .into_response();
        }
        (_, Some(hex)) => match hexutil::decode(hex) {
            Ok(d) => Some(d),
            Err(e) => {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(serde_json::json!({"error": {"category": "bad_request",
                        "message": e}})),
                )
                    .into_response();
            }
        },
        _ => None,
    };

    let response = shared.lock().unwrap().submit(Request {
        request_id: req.request_id,
        op,
        page: req.page,
        data,
    });

    match response.result {
        Ok(ok) => Json(AccessResponse {
            request_id: response.request_id,
            seq: response.seq,
            status: "ok",
            outcome: Some(outcome_name(ok.outcome)),
            data_hex: ok.data.as_deref().map(hexutil::encode),
            error: None,
        })
        .into_response(),
        Err(e) => {
            let status = match e.category() {
                crate::error::ErrorCategory::BadRequest => StatusCode::BAD_REQUEST,
                crate::error::ErrorCategory::Store => StatusCode::BAD_GATEWAY,
                _ => StatusCode::CONFLICT,
            };
            (
                status,
                Json(AccessResponse {
                    request_id: response.request_id,
                    seq: response.seq,
                    status: "error",
                    outcome: None,
                    data_hex: None,
                    error: Some(ErrorBody {
                        category: serde_json::to_value(e.category())
                            .ok()
                            .and_then(|v| v.as_str().map(str::to_owned))
                            .unwrap_or_else(|| "unknown".into()),
                        message: e.to_string(),
                    }),
                }),
            )
                .into_response()
        }
    }
}

#[derive(Debug, Deserialize)]
struct ResizeRequest {
    capacity: usize,
}

async fn resize(State(shared): State<Shared>, Json(req): Json<ResizeRequest>) -> impl IntoResponse {
    match shared.lock().unwrap().resize(req.capacity) {
        Ok(()) => Json(serde_json::json!({"status": "ok", "capacity": req.capacity})).into_response(),
        Err(e) => (
            StatusCode::CONFLICT,
            Json(serde_json::json!({"status": "error",
                "error": {"category": serde_json::to_value(e.category()).unwrap_or_default(),
                          "message": e.to_string()}})),
        )
            .into_response(),
    }
}

async fn stats(State(shared): State<Shared>) -> impl IntoResponse {
    let engine = shared.lock().unwrap();
    Json(serde_json::json!({
        "capacity": engine.cache().capacity(),
        "p": engine.cache().p(),
        "stats": engine.stats(),
    }))
}

async fn stats_history(State(shared): State<Shared>) -> impl IntoResponse {
    let engine = shared.lock().unwrap();
    let samples: Vec<_> = engine.samples().into_iter().cloned().collect();
    Json(serde_json::json!({ "samples": samples }))
}

async fn state(State(shared): State<Shared>) -> impl IntoResponse {
    let engine = shared.lock().unwrap();
    let cache = engine.cache();
    Json(serde_json::json!({
        "capacity": cache.capacity(),
        "p": cache.p(),
        "lists": cache.lists(),
        "dirty_pages": cache.dirty_pages(),
    }))
}

#[derive(Debug, Deserialize)]
struct DiagQuery {
    limit: Option<usize>,
    decision: Option<String>,
}

async fn diagnostics(
    State(shared): State<Shared>,
    Query(q): Query<DiagQuery>,
) -> impl IntoResponse {
    let engine = shared.lock().unwrap();
    let records: Vec<_> = engine
        .diagnostics(q.limit.unwrap_or(50).min(1000), q.decision.as_deref())
        .into_iter()
        .cloned()
        .collect();
    Json(serde_json::json!({ "records": records }))
}

async fn snapshot(State(shared): State<Shared>) -> impl IntoResponse {
    let path = shared.lock().unwrap().snapshot_path().to_path_buf();
    match shared.lock().unwrap().snapshot(&path) {
        Ok(()) => Json(serde_json::json!({"status": "ok", "path": path.display().to_string()}))
            .into_response(),
        Err(e) => (
            StatusCode::CONFLICT,
            Json(serde_json::json!({"status": "error", "message": e.to_string()})),
        )
            .into_response(),
    }
}

async fn restore(State(shared): State<Shared>) -> impl IntoResponse {
    let path = shared.lock().unwrap().snapshot_path().to_path_buf();
    match shared.lock().unwrap().restore(&path) {
        Ok(()) => Json(serde_json::json!({"status": "ok"})).into_response(),
        Err(e) => (
            StatusCode::CONFLICT,
            Json(serde_json::json!({"status": "error", "message": e.to_string()})),
        )
            .into_response(),
    }
}
