//! Axum HTTP transport. The server is a *dumb relay plus state machine*:
//! it only ever sees blinded points, enforces the session state machine,
//! validates every point, and writes redacted audit records.

use std::sync::Arc;

use axum::extract::{Path, Request, State};
use axum::http::{HeaderValue, StatusCode};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Extension, Json, Router};
use rand_core::{OsRng, RngCore};
use serde::{Deserialize, Serialize};

use crate::audit::{self, AuditRecord};
use crate::config::ServerConfig;
use crate::crypto;
use crate::error::PsiError;
use crate::store::{SessionState, Store};

pub struct AppState {
    pub store: Store,
    pub max_set_size: usize,
}

#[derive(Clone)]
pub struct RequestId(pub String);

pub fn build_router(state: Arc<AppState>, max_body_bytes: usize) -> Router {
    Router::new()
        .route("/v1/sessions", post(create_session))
        .route("/v1/sessions/{id}", get(get_session))
        .route("/v1/sessions/{id}/submissions/a", post(submit_a))
        .route("/v1/sessions/{id}/submissions/b", post(submit_b))
        .route("/v1/sessions/{id}/messages/a", get(messages_for_b))
        .route("/v1/sessions/{id}/messages/b", get(messages_for_a))
        .route("/v1/sessions/{id}/audit", get(get_audit))
        .layer(axum::extract::DefaultBodyLimit::max(max_body_bytes))
        .layer(middleware::from_fn(request_id_middleware))
        .with_state(state)
}

pub async fn run(config: ServerConfig) -> Result<(), String> {
    let store = Store::open(&config.db_path).map_err(|e| e.to_string())?;
    let state = Arc::new(AppState {
        store,
        max_set_size: config.max_set_size,
    });
    let app = build_router(state, config.max_body_bytes);
    let listener = tokio::net::TcpListener::bind(&config.bind_addr)
        .await
        .map_err(|e| format!("bind {}: {e}", config.bind_addr))?;
    tracing::info!(bind = %config.bind_addr, db = %config.db_path, "psi-server listening");
    axum::serve(listener, app)
        .await
        .map_err(|e| e.to_string())
}

async fn request_id_middleware(mut req: Request, next: Next) -> Response {
    let mut bytes = [0u8; 8];
    OsRng.fill_bytes(&mut bytes);
    let request_id = hex::encode(bytes);
    req.extensions_mut().insert(RequestId(request_id.clone()));
    let mut resp = next.run(req).await;
    if let Ok(value) = HeaderValue::from_str(&request_id) {
        resp.headers_mut()
            .insert(axum::http::header::HeaderName::from_static("x-request-id"), value);
    }
    resp
}

// ---------- error mapping ----------

#[derive(Serialize)]
struct ErrorBody {
    error: ErrorDetail,
}

#[derive(Serialize)]
struct ErrorDetail {
    code: &'static str,
    message: String,
    request_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    session_state: Option<String>,
}

fn api_error(request_id: &str, err: PsiError, session_state: Option<SessionState>) -> Response {
    let status = StatusCode::from_u16(err.http_status()).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    let body = ErrorBody {
        error: ErrorDetail {
            code: err.code(),
            message: err.to_string(),
            request_id: request_id.to_string(),
            session_state: session_state.map(|s| s.as_str().to_string()),
        },
    };
    (status, Json(body)).into_response()
}


/// Best-effort audit write: a storage failure here must not fail the request,
/// but it is never silent — it is logged with the event name.
fn write_audit(state: &AppState, session: Option<&str>, rid: &str, event: &str, outcome: &str, detail: &str) {
    if let Err(e) = state.store.add_audit(session, rid, event, outcome, detail) {
        tracing::warn!(error = %e, event, outcome, "failed to write audit record");
    }
}

// ---------- request/response types ----------

#[derive(Serialize)]
struct CreateSessionResponse {
    session_id: String,
    state: &'static str,
}

#[derive(Serialize)]
struct SessionStatusResponse {
    session_id: String,
    state: String,
}

#[derive(Deserialize)]
struct SubmitPointsRequest {
    /// Hex-encoded 32-byte ristretto255 points.
    points: Vec<String>,
}

#[derive(Deserialize)]
struct SubmitBRequest {
    b_blinded: Vec<String>,
    a_doubly: Vec<String>,
}

#[derive(Serialize)]
struct SubmitAResponse {
    state: &'static str,
    accepted: usize,
    /// Duplicates seen in A's submission. NOT removed: B's response is
    /// index-aligned with A's array, so order and count must be preserved.
    duplicates_observed: usize,
}

#[derive(Serialize)]
struct SubmitBResponse {
    state: &'static str,
    accepted: usize,
    duplicates_removed: usize,
}

#[derive(Serialize)]
struct MessagesAResponse {
    state: String,
    /// A's blinded set; present once state == a_submitted or completed.
    points: Option<Vec<String>>,
}

#[derive(Serialize)]
struct MessagesBResponse {
    state: String,
    b_blinded: Option<Vec<String>>,
    a_doubly: Option<Vec<String>>,
}

#[derive(Serialize)]
struct AuditResponse {
    session_id: String,
    records: Vec<AuditRecord>,
}

// ---------- handlers ----------

async fn create_session(
    State(state): State<Arc<AppState>>,
    Extension(rid): Extension<RequestId>,
) -> Response {
    let session_id = crypto::generate_session_id();
    let session_hex = hex::encode(session_id);
    if let Err(e) = state.store.create_session(&session_hex) {
        return api_error(&rid.0, e, None);
    }
    write_audit(&state, 
        Some(&session_hex),
        &rid.0,
        "session_created",
        "accepted",
        "new session; state=created",
    );
    (
        StatusCode::CREATED,
        Json(CreateSessionResponse {
            session_id: session_hex,
            state: SessionState::Created.as_str(),
        }),
    )
        .into_response()
}

async fn get_session(
    State(state): State<Arc<AppState>>,
    Extension(rid): Extension<RequestId>,
    Path(id): Path<String>,
) -> Response {
    match state.store.session_state(&id) {
        Ok(s) => Json(SessionStatusResponse {
            session_id: id,
            state: s.as_str().to_string(),
        })
        .into_response(),
        Err(e) => api_error(&rid.0, e, None),
    }
}

async fn submit_a(
    State(state): State<Arc<AppState>>,
    Extension(rid): Extension<RequestId>,
    Path(id): Path<String>,
    Json(body): Json<SubmitPointsRequest>,
) -> Response {
    let current = state.store.session_state(&id).ok();
    let result = decode_hex_points(&body.points, state.max_set_size)
        .and_then(|points| {
            // Validate every point before anything is stored.
            crypto::decode_points(&points)?;
            state.store.save_a_submission(&id, &points).map(|removed| {
                let digest = audit::payload_digest(&points);
                (points.len(), removed, digest)
            })
        });
    match result {
        Ok((accepted, observed, digest)) => {
            write_audit(&state, 
                Some(&id),
                &rid.0,
                "a_submission",
                "accepted",
                &format!("points={accepted} duplicates_observed={observed} payload_sha256={digest}"),
            );
            Json(SubmitAResponse {
                state: SessionState::ASubmitted.as_str(),
                accepted,
                duplicates_observed: observed,
            })
            .into_response()
        }
        Err(e) => {
            write_audit(&state, 
                Some(&id),
                &rid.0,
                "a_submission",
                "rejected",
                &format!("code={} reason={}", e.code(), e),
            );
            api_error(&rid.0, e, current)
        }
    }
}

async fn submit_b(
    State(state): State<Arc<AppState>>,
    Extension(rid): Extension<RequestId>,
    Path(id): Path<String>,
    Json(body): Json<SubmitBRequest>,
) -> Response {
    let current = state.store.session_state(&id).ok();
    let result = (|| {
        let b_blinded = decode_hex_points(&body.b_blinded, state.max_set_size)?;
        let a_doubly = decode_hex_points(&body.a_doubly, state.max_set_size)?;
        crypto::decode_points(&b_blinded)?;
        crypto::decode_points(&a_doubly)?;
        let outcome = state.store.save_b_response(&id, &b_blinded, &a_doubly)?;
        Ok::<_, PsiError>((
            b_blinded.len(),
            outcome.b_duplicates_removed,
            audit::payload_digest(&b_blinded),
            audit::payload_digest(&a_doubly),
        ))
    })();
    match result {
        Ok((accepted, removed, b_digest, d_digest)) => {
            write_audit(&state, 
                Some(&id),
                &rid.0,
                "b_response",
                "accepted",
                &format!(
                    "b_points={accepted} duplicates_removed={removed} \
                     b_payload_sha256={b_digest} a_doubly_sha256={d_digest}"
                ),
            );
            Json(SubmitBResponse {
                state: SessionState::Completed.as_str(),
                accepted,
                duplicates_removed: removed,
            })
            .into_response()
        }
        Err(e) => {
            write_audit(&state, 
                Some(&id),
                &rid.0,
                "b_response",
                "rejected",
                &format!("code={} reason={}", e.code(), e),
            );
            api_error(&rid.0, e, current)
        }
    }
}

async fn messages_for_b(
    State(state): State<Arc<AppState>>,
    Extension(rid): Extension<RequestId>,
    Path(id): Path<String>,
) -> Response {
    let session_state = match state.store.session_state(&id) {
        Ok(s) => s,
        Err(e) => return api_error(&rid.0, e, None),
    };
    let points = match state.store.a_submission(&id) {
        Ok(p) => p,
        Err(e) => return api_error(&rid.0, e, Some(session_state)),
    };
    Json(MessagesAResponse {
        state: session_state.as_str().to_string(),
        points: points.map(|ps| ps.iter().map(hex::encode).collect()),
    })
    .into_response()
}

async fn messages_for_a(
    State(state): State<Arc<AppState>>,
    Extension(rid): Extension<RequestId>,
    Path(id): Path<String>,
) -> Response {
    let session_state = match state.store.session_state(&id) {
        Ok(s) => s,
        Err(e) => return api_error(&rid.0, e, None),
    };
    let response = match state.store.b_response(&id) {
        Ok(r) => r,
        Err(e) => return api_error(&rid.0, e, Some(session_state)),
    };
    let (b_blinded, a_doubly) = match response {
        Some((b, d)) => (
            Some(b.iter().map(hex::encode).collect::<Vec<_>>()),
            Some(d.iter().map(hex::encode).collect::<Vec<_>>()),
        ),
        None => (None, None),
    };
    Json(MessagesBResponse {
        state: session_state.as_str().to_string(),
        b_blinded,
        a_doubly,
    })
    .into_response()
}

async fn get_audit(
    State(state): State<Arc<AppState>>,
    Extension(rid): Extension<RequestId>,
    Path(id): Path<String>,
) -> Response {
    if let Err(e) = state.store.session_state(&id) {
        return api_error(&rid.0, e, None);
    }
    match state.store.audit_for(&id) {
        Ok(records) => Json(AuditResponse {
            session_id: id,
            records,
        })
        .into_response(),
        Err(e) => api_error(&rid.0, e, None),
    }
}

// ---------- helpers ----------

fn decode_hex_points(hexes: &[String], max_set_size: usize) -> Result<Vec<[u8; 32]>, PsiError> {
    if hexes.len() > max_set_size {
        return Err(PsiError::SetTooLarge {
            got: hexes.len(),
            max: max_set_size,
        });
    }
    let mut out = Vec::with_capacity(hexes.len());
    for (i, h) in hexes.iter().enumerate() {
        let bytes = hex::decode(h)
            .map_err(|_| PsiError::BadRequest(format!("point {i}: invalid hex")))?;
        let arr: [u8; 32] = bytes
            .as_slice()
            .try_into()
            .map_err(|_| PsiError::BadPointLength {
                index: i,
                got: bytes.len(),
            })?;
        out.push(arr);
    }
    Ok(out)
}
