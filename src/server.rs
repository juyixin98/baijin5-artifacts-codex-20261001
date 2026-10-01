//! HTTP layer (Axum). Thin transport over [`crate::api::run_pipeline`].
//!
//! Every request carries/gets a `x-request-id` which is returned in the body
//! and logged at each meaningful step, so interface results and logs can be
//! correlated.

use std::sync::Arc;

use axum::{
    body::Bytes,
    extract::State,
    http::{HeaderMap, HeaderValue, StatusCode},
    routing::{get, post},
    Json, Router,
};
use serde_json::{json, Value};
use tracing::{debug, info, warn};

use crate::api::{run_pipeline, RawRequest};
use crate::error::ErrorKind;
use crate::state::{AppState, RequestCtx};

pub fn router(state: AppState) -> Router {
    let limit = state.body_limit_layer();
    Router::new()
        .route("/health", get(health))
        .route("/version", get(version))
        .route("/query", post(query))
        .layer(limit)
        .with_state(Arc::new(state))
}

async fn health(State(s): State<Arc<AppState>>) -> Json<Value> {
    Json(json!({
        "status": "ok",
        "version": s.version,
        "location": s.location,
    }))
}

async fn version(State(s): State<Arc<AppState>>) -> Json<Value> {
    Json(json!({
        "name": env!("CARGO_PKG_NAME"),
        "version": s.version,
        "location": s.location,
        "arrow2": "0.18.0",
        "axum": "0.7.9",
    }))
}

async fn query(
    State(s): State<Arc<AppState>>,
    headers: HeaderMap,
    body: Bytes,
) -> (StatusCode, HeaderMap, Json<Value>) {
    let incoming_id = headers
        .get("x-request-id")
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string());
    let ctx = incoming_id
        .as_ref()
        .map(|id| RequestCtx::with_id(id.clone()))
        .unwrap_or_else(RequestCtx::new);
    let request_id = ctx.request_id.clone();

    info!(request_id = %request_id, location = %s.location, version = %s.version, "query received");

    // Deserialize explicitly so a malformed body gets a classified error.
    let raw: RawRequest = match serde_json::from_slice(&body) {
        Ok(r) => r,
        Err(e) => {
            warn!(request_id = %request_id, error = %e, "malformed request body");
            let payload = json!({
                "request_id": request_id,
                "version": s.version,
                "location": s.location,
                "status": "error",
                "errors": [{
                    "kind": ErrorKind::InvalidRequest.as_str(),
                    "message": format!("request body is not valid: {e}"),
                    "at_step": "parse_request",
                    "engine": Value::Null,
                }],
                "uncertainties": [],
            });
            return (
                StatusCode::BAD_REQUEST,
                rid_header(&request_id),
                Json(payload),
            );
        }
    };

    debug!(request_id = %request_id, "request deserialized; running pipeline");
    let (out, code) = run_pipeline(raw, &s, &ctx);

    for st in &out.steps {
        debug!(request_id = %request_id, step = %st.step, elapsed_ms = st.elapsed_ms, detail = %st.detail, "step");
    }

    if out.status == "ok" {
        info!(
            request_id = %request_id,
            form = ?out.form,
            rows = out.result.as_ref().map(|r| r.row_count),
            elapsed_ms = ctx.elapsed_ms(),
            "query ok"
        );
    } else {
        warn!(
            request_id = %request_id,
            errors = ?out.errors.iter().map(|e| e.kind.clone()).collect::<Vec<_>>(),
            elapsed_ms = ctx.elapsed_ms(),
            "query failed"
        );
    }

    let value = serde_json::to_value(&out).unwrap_or_else(|e| {
        json!({
            "request_id": request_id,
            "status": "error",
            "errors": [{ "kind": ErrorKind::Internal.as_str(), "message": e.to_string() }],
        })
    });
    (
        StatusCode::from_u16(code).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
        rid_header(&request_id),
        Json(value),
    )
}

fn rid_header(id: &str) -> HeaderMap {
    let mut h = HeaderMap::new();
    if let Ok(v) = HeaderValue::from_str(id) {
        h.insert("x-request-id", v);
    }
    h
}
