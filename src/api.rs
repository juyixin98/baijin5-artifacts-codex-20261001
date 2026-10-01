//! HTTP transport (Axum).
//!
//! Routes:
//!
//! - `GET  /health`                 liveness and config summary;
//! - `GET  /relations`              catalog fixture names;
//! - `POST /admin/relations`        register local fixture relations;
//! - `POST /query`                  JSON join endpoint;
//! - `POST /query/arrow`            same query, body is an Arrow2 IPC stream.
//!
//! All decisions use the shared [`crate::validate::execute`] entry point, so
//! HTTP and tests cannot drift apart. Errors carry the stable
//! [`ErrorCode`] and the correlation id recorded in diagnostics.

use std::sync::Arc;

use axum::{
    body::Bytes,
    extract::State,
    http::{header, HeaderMap, HeaderValue, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde::Serialize;

use crate::diagnostics::{DecisionRecord, KeyState, RequestId};
use crate::error::ErrorCode;
use crate::state::AppState;
use crate::validate::{execute, parse_request, QueryRequest, ValidateDecision};

/// Build the application router around shared [`AppState`].
pub fn build_router(state: Arc<AppState>) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/relations", get(list_relations))
        .route("/admin/relations", post(register_relations))
        .route("/query", post(query_json))
        .route("/query/arrow", post(query_arrow))
        .with_state(state)
}

async fn health(State(state): State<Arc<AppState>>) -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "status": "ok",
        "service": "leapfrog-triejoin",
        "max_relations": state.config.max_relations,
        "default_limit": state.config.default_limit,
        "max_limit": state.config.max_limit,
        "max_access_budget": state.config.max_access_budget,
    }))
}

async fn list_relations(State(state): State<Arc<AppState>>) -> Json<serde_json::Value> {
    let catalog = state.catalog.read().expect("catalog lock poisoned");
    Json(serde_json::json!({ "relations": catalog.names() }))
}

#[derive(Serialize)]
struct ErrorBody {
    request_id: String,
    decision: &'static str,
    error: ErrorView,
}

#[derive(Serialize)]
struct ErrorView {
    code: String,
    message: String,
}

fn rejection_response(record: &DecisionRecord, code: ErrorCode, message: &str) -> Response {
    let status = StatusCode::from_u16(code.http_status()).unwrap_or(StatusCode::BAD_REQUEST);
    let reason = record.reasons.first();
    let body = ErrorBody {
        request_id: record.request_id.clone(),
        decision: "rejected",
        error: ErrorView {
            code: code.as_str().to_string(),
            message: reason
                .map(|r| r.detail.clone())
                .unwrap_or_else(|| message.to_string()),
        },
    };
    (status, Json(body)).into_response()
}

async fn register_relations(State(state): State<Arc<AppState>>, body: Bytes) -> Response {
    let request_id = RequestId::new();
    let request = match parse_request_payload(&request_id, &body) {
        Ok(req) => req,
        Err(response) => return response,
    };

    let mut registered = Vec::new();
    {
        let mut catalog = state.catalog.write().expect("catalog lock poisoned");
        for spec in request.relations {
            match crate::validate::parse_relation(spec.clone()) {
                Ok(relation) => match catalog.register(relation) {
                    Ok(()) => registered.push(spec.name),
                    Err(err) => {
                        let record = DecisionRecord::rejected(
                            &request_id,
                            KeyState::default(),
                            err.code,
                            err.to_string(),
                        );
                        return rejection_response(&record, err.code, "registration failed");
                    }
                },
                Err(err) => {
                    let record = DecisionRecord::rejected(
                        &request_id,
                        KeyState::default(),
                        err.code,
                        err.to_string(),
                    );
                    return rejection_response(&record, err.code, "invalid relation");
                }
            }
        }
    }
    (
        StatusCode::CREATED,
        Json(serde_json::json!({ "request_id": request_id.0, "registered": registered })),
    )
        .into_response()
}

/// Shared parse step that turns parse failures into HTTP responses. The
/// rejection's diagnostic record is already logged by [`DecisionRecord`].
// The error *is* an HTTP response here by design (transport boundary), so its
// size is intrinsic; boxing it would only add an allocation.
#[allow(clippy::result_large_err)]
fn parse_request_payload(request_id: &RequestId, body: &[u8]) -> Result<QueryRequest, Response> {
    match parse_request(body) {
        Ok(request) => Ok(request),
        Err(err) => {
            let record = DecisionRecord::rejected(
                request_id,
                KeyState::default(),
                err.code,
                err.to_string(),
            );
            Err(rejection_response(
                &record,
                err.code,
                "invalid request body",
            ))
        }
    }
}

async fn query_json(State(state): State<Arc<AppState>>, body: Bytes) -> Response {
    let request_id = RequestId::new();
    let request = match parse_request_payload(&request_id, &body) {
        Ok(req) => req,
        Err(response) => return response,
    };
    match execute(&state.config, &state, request) {
        ValidateDecision::Ran(payload) => {
            let status = match payload.outcome.decision {
                crate::diagnostics::Decision::Undecidable => StatusCode::ACCEPTED,
                _ => StatusCode::OK,
            };
            (status, Json(payload.outcome)).into_response_with_record(&payload.record)
        }
        ValidateDecision::Rejected(record) => {
            let code = record
                .error_code
                .as_deref()
                .and_then(error_code_from_str)
                .unwrap_or(ErrorCode::InvalidRequest);
            rejection_response(&record, code, "query rejected")
        }
    }
}

async fn query_arrow(State(state): State<Arc<AppState>>, body: Bytes) -> Response {
    let request_id = RequestId::new();
    let request = match parse_request_payload(&request_id, &body) {
        Ok(req) => req,
        Err(response) => return response,
    };
    match execute(&state.config, &state, request) {
        ValidateDecision::Ran(payload) => {
            let ipc = match payload.typed.to_ipc_stream() {
                Ok(bytes) => bytes,
                Err(err) => {
                    let record = DecisionRecord::rejected(
                        &request_id,
                        KeyState::default(),
                        err.code,
                        err.to_string(),
                    );
                    return rejection_response(&record, err.code, "arrow encoding failed");
                }
            };

            let mut headers = HeaderMap::new();
            headers.insert(
                header::CONTENT_TYPE,
                HeaderValue::from_static("application/vnd.apache.arrow.stream"),
            );
            headers.insert(
                "x-request-id",
                HeaderValue::from_str(&payload.outcome.request_id).unwrap(),
            );
            headers.insert(
                "x-decision",
                HeaderValue::from_str(match payload.outcome.decision {
                    crate::diagnostics::Decision::Accepted => "accepted",
                    crate::diagnostics::Decision::Rejected => "rejected",
                    crate::diagnostics::Decision::Undecidable => "undecidable",
                })
                .unwrap(),
            );
            headers.insert(
                "x-row-count",
                HeaderValue::from_str(&payload.outcome.row_count.to_string()).unwrap(),
            );
            headers.insert(
                "x-stop-reason",
                HeaderValue::from_str(&payload.outcome.stats.stop_reason).unwrap(),
            );
            if let Some(cursor) = &payload.outcome.next_cursor {
                headers.insert("x-next-cursor", HeaderValue::from_str(cursor).unwrap());
            }
            headers.insert(
                "x-trie-seeks",
                HeaderValue::from_str(&payload.outcome.stats.trie_seeks.to_string()).unwrap(),
            );
            headers.insert("x-intermediate-tuples", HeaderValue::from_str("0").unwrap());

            let status = match payload.outcome.decision {
                crate::diagnostics::Decision::Undecidable => StatusCode::ACCEPTED,
                _ => StatusCode::OK,
            };
            (status, headers, Bytes::from(ipc)).into_response_with_record(&payload.record)
        }
        ValidateDecision::Rejected(record) => {
            let code = record
                .error_code
                .as_deref()
                .and_then(error_code_from_str)
                .unwrap_or(ErrorCode::InvalidRequest);
            rejection_response(&record, code, "query rejected")
        }
    }
}

fn error_code_from_str(s: &str) -> Option<ErrorCode> {
    Some(match s {
        "malformed_json" => ErrorCode::MalformedJson,
        "invalid_request" => ErrorCode::InvalidRequest,
        "unknown_relation" => ErrorCode::UnknownRelation,
        "type_mismatch" => ErrorCode::TypeMismatch,
        "disjoint_schema" => ErrorCode::DisjointSchema,
        "null_key" => ErrorCode::NullKey,
        "unsupported_shape" => ErrorCode::UnsupportedShape,
        "invalid_cursor" => ErrorCode::InvalidCursor,
        "internal_error" => ErrorCode::Internal,
        _ => return None,
    })
}

/// Marker trait so successful responses can acknowledge the decision record
/// (the record itself is already logged by [`DecisionRecord`]; we only attach
/// the correlation id as a response header here).
trait WithRecord {
    fn into_response_with_record(self, record: &DecisionRecord) -> Response;
}

impl<T: IntoResponse> WithRecord for T {
    fn into_response_with_record(self, record: &DecisionRecord) -> Response {
        let mut response = self.into_response();
        if let Ok(value) = HeaderValue::from_str(&record.request_id) {
            response.headers_mut().insert("x-request-id", value);
        }
        response
    }
}
