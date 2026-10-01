//! HTTP handlers with per-request correlation ids.

use axum::body::Bytes;
use axum::extract::State;
use axum::http::{HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;

use crate::diagnostics::{
    log_accept, log_indeterminate, log_reject, log_resource, BudgetSnapshot, RequestId,
};
use crate::error::{ErrorKind, PctlError};
use crate::exec::{ExecOutcome, ExecStats};
use crate::resources::CancellationToken;
use crate::spec::{Diagnostics, ErrorBody, GroupResult, QueryRequest, QueryResponse, ResumeToken};

use super::AppState;

pub async fn health() -> &'static str {
    "pctl ok\n"
}

pub async fn query(State(state): State<AppState>, headers: HeaderMap, body: Bytes) -> Response {
    let rid = headers
        .get("x-request-id")
        .and_then(|v| v.to_str().ok())
        .filter(|s| !s.is_empty() && s.len() <= 64)
        .map(|s| RequestId(s.to_string()))
        .unwrap_or_else(RequestId::new);

    let resp = handle_query(state, &rid, body).await;
    attach_rid(resp, &rid)
}

async fn handle_query(state: AppState, rid: &RequestId, body: Bytes) -> Response {
    let req: QueryRequest = match serde_json::from_slice(&body) {
        Ok(req) => req,
        Err(e) => {
            log_reject(rid, "decode", "malformed_json", &e.to_string());
            return error_response(
                rid,
                StatusCode::BAD_REQUEST,
                ErrorKind::InvalidRequest,
                "malformed_json".to_string(),
                e.to_string(),
                None,
            );
        }
    };

    // Only structural facts are logged — never column payloads.
    log_accept(
        rid,
        "received",
        &format!(
            "group_by={} operators={} rows={}",
            req.group_by,
            req.operators.len(),
            req.columns.first().map(|c| c.values.len()).unwrap_or(0)
        ),
    );

    // CPU/IO work runs on the blocking pool to keep the async runtime free.
    let cfg = (*state.config).clone();
    let rid2 = rid.clone();
    let outcome = tokio::task::spawn_blocking(move || {
        let cancel = CancellationToken::new();
        crate::exec::execute_request(&req, &rid2, &cfg, cancel)
    })
    .await;

    match outcome {
        Err(join_err) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({
                "request_id": rid.to_string(),
                "status": "error",
                "error": { "kind": "internal", "code": "worker_join",
                           "message": format!("worker join failure: {join_err}") },
            })),
        )
            .into_response(),
        Ok(Ok(ExecOutcome::Complete(groups, stats))) => {
            log_resource(
                rid,
                "complete",
                BudgetSnapshot {
                    used: stats.resident_high_water,
                    high_water: stats.resident_high_water,
                    limit: stats.resident_budget,
                },
                &format!(
                    "groups={} runs={} spilled_bytes={}",
                    stats.groups_tracked, stats.runs_spilled, stats.bytes_spilled
                ),
            );
            ok_response(
                rid,
                "complete",
                groups,
                diagnostics_from(stats, false, None),
            )
        }
        Ok(Ok(ExecOutcome::Cancelled {
            resume,
            cursor,
            stats,
        })) => {
            log_accept(
                rid,
                "checkpoint",
                &format!("resumable cursor={cursor} dir={}", resume.spill_dir),
            );
            ok_response(
                rid,
                "cancelled",
                Vec::new(),
                diagnostics_from(stats, true, Some((resume, cursor))),
            )
        }
        Ok(Err(e)) => map_error(rid, e),
    }
}

fn diagnostics_from(
    stats: ExecStats,
    resumable: bool,
    resume: Option<(ResumeToken, u64)>,
) -> Diagnostics {
    let (token, cursor) = match resume {
        Some((t, c)) => (Some(t), c),
        None => (None, 0),
    };
    Diagnostics {
        resident_budget_bytes: stats.resident_budget,
        resident_high_water_bytes: stats.resident_high_water,
        groups_tracked: stats.groups_tracked,
        groups_rejected_by_cap: stats.groups_rejected_by_cap,
        runs_spilled: stats.runs_spilled,
        bytes_spilled: stats.bytes_spilled,
        resumable,
        resume: token,
        ordinal_cursor: cursor,
    }
}

fn ok_response(
    rid: &RequestId,
    status: &'static str,
    groups: Vec<GroupResult>,
    diagnostics: Diagnostics,
) -> Response {
    Json(QueryResponse {
        request_id: rid.0.clone(),
        status,
        groups,
        error: None,
        diagnostics,
    })
    .into_response()
}

fn map_error(rid: &RequestId, e: PctlError) -> Response {
    match e.kind {
        ErrorKind::Validation | ErrorKind::InvalidRequest => {
            log_reject(
                rid,
                "validate",
                &e.code,
                &e.field
                    .as_ref()
                    .map(|f| format!("field={f}"))
                    .unwrap_or_default(),
            );
            error_response(
                rid,
                StatusCode::BAD_REQUEST,
                e.kind,
                e.code,
                e.message,
                e.field,
            )
        }
        ErrorKind::Indeterminate => {
            log_indeterminate(rid, "execute", &e.code, &e.message);
            error_response(
                rid,
                StatusCode::UNPROCESSABLE_ENTITY,
                e.kind,
                e.code,
                e.message,
                e.field,
            )
        }
        ErrorKind::Resource => {
            log_reject(rid, "execute", &e.code, &e.message);
            error_response(
                rid,
                StatusCode::INSUFFICIENT_STORAGE,
                e.kind,
                e.code,
                e.message,
                e.field,
            )
        }
        ErrorKind::Cancelled => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"request_id": rid.to_string(),
                "status": "error",
                "error": {"kind": "internal", "code": "unexpected_cancel",
                          "message": e.message}})),
        )
            .into_response(),
    }
}

fn error_response(
    rid: &RequestId,
    status: StatusCode,
    kind: ErrorKind,
    code: String,
    message: String,
    field: Option<String>,
) -> Response {
    let body = QueryResponse {
        request_id: rid.0.clone(),
        status: "error",
        groups: Vec::new(),
        error: Some(ErrorBody {
            kind: kind.as_str().to_string(),
            code,
            message,
            field,
        }),
        diagnostics: Diagnostics {
            resident_budget_bytes: 0,
            resident_high_water_bytes: 0,
            groups_tracked: 0,
            groups_rejected_by_cap: 0,
            runs_spilled: 0,
            bytes_spilled: 0,
            resumable: false,
            resume: None,
            ordinal_cursor: 0,
        },
    };
    (status, Json(body)).into_response()
}

fn attach_rid(mut resp: Response, rid: &RequestId) -> Response {
    if let Ok(v) = HeaderValue::from_str(&rid.0) {
        resp.headers_mut().insert("x-request-id", v);
    }
    resp
}
