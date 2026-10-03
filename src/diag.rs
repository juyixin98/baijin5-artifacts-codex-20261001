//! Diagnostics and demo HTTP interface (axum).
//!
//! Every response carries record/request identifiers and the key state that
//! explains *why* the runtime accepted, rejected or could not decide. All
//! caller-supplied data is redacted: `user_data` appears only as a truncated
//! SHA-256 fingerprint and file paths only as basenames.

use crate::adapter::{CancelAck, IoAdapter};
use crate::journal;
use crate::model::{CompletionRecord, Handle, OpKind, RecordId, Submission};
use crate::runtime::{CancelError, RuntimeCore, SubmitError};
use axum::extract::{Path as AxumPath, State};
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

/// Shared state behind the HTTP interface.
pub struct DiagState<A: IoAdapter> {
    pub core: Mutex<RuntimeCore<A>>,
    pub journal_path: PathBuf,
}

impl<A: IoAdapter> DiagState<A> {
    pub fn new(core: RuntimeCore<A>, journal_path: PathBuf) -> Arc<Self> {
        Arc::new(Self {
            core: Mutex::new(core),
            journal_path,
        })
    }
}

/// Truncated SHA-256 fingerprint of a `user_data` value. The raw value never
/// leaves the process boundary.
pub fn redact_user_data(user_data: u64) -> String {
    let digest = Sha256::digest(user_data.to_le_bytes());
    format!("sha256:{}", hex_prefix(&digest, 8))
}

/// Only the file name component is shown; directories are caller-sensitive.
fn redact_path(path: &std::path::Path) -> String {
    path.file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| "<unnamed>".to_string())
}

fn hex_prefix(bytes: &[u8], take: usize) -> String {
    bytes
        .iter()
        .take(take)
        .map(|b| format!("{b:02x}"))
        .collect()
}

#[derive(Serialize)]
struct RecordView {
    record_id: u64,
    slot: u16,
    generation: u32,
    user_data_fingerprint: String,
    op: &'static str,
    path_basename: Option<String>,
    cancel_requested: bool,
    submitted_at_ms: u64,
    completed_at_ms: u64,
    outcome: &'static str,
    outcome_detail: Option<String>,
}

impl From<&CompletionRecord> for RecordView {
    fn from(r: &CompletionRecord) -> Self {
        let path_basename = match &r.op {
            OpKind::ReadFile { path } => Some(redact_path(path)),
            OpKind::WriteFile { path, .. } => Some(redact_path(path)),
            OpKind::Nop => None,
        };
        let outcome_detail = match &r.outcome {
            crate::model::CompletionOutcome::Success { bytes } => Some(format!("{bytes} bytes")),
            crate::model::CompletionOutcome::Failed { message } => Some(message.clone()),
            _ => None,
        };
        Self {
            record_id: r.record_id.0,
            slot: r.handle.slot,
            generation: r.handle.generation.0,
            user_data_fingerprint: redact_user_data(r.user_data),
            op: r.op.name(),
            path_basename,
            cancel_requested: r.cancel_requested,
            submitted_at_ms: r.submitted_at_ms,
            completed_at_ms: r.completed_at_ms,
            outcome: r.outcome.kind(),
            outcome_detail,
        }
    }
}

#[derive(Serialize)]
struct ErrorBody {
    error: &'static str,
    reason: String,
}

fn error_response(status: StatusCode, error: &'static str, reason: String) -> axum::response::Response {
    (status, Json(ErrorBody { error, reason })).into_response()
}

// --- diagnostics -----------------------------------------------------------

#[derive(Serialize)]
struct StateView {
    capacity: usize,
    in_flight: usize,
    buffers: crate::resources::BufferStats,
    slots: Vec<crate::ring::SlotView>,
}

async fn get_state<A: IoAdapter>(State(state): State<Arc<DiagState<A>>>) -> impl IntoResponse {
    let core = state.core.lock().expect("core mutex");
    Json(StateView {
        capacity: core.capacity(),
        in_flight: core.in_flight(),
        buffers: core.buffer_stats(),
        slots: core.slot_snapshot(),
    })
}

async fn get_records<A: IoAdapter>(State(state): State<Arc<DiagState<A>>>) -> impl IntoResponse {
    let core = state.core.lock().expect("core mutex");
    let records: Vec<RecordView> = core.recent_records().iter().map(RecordView::from).collect();
    Json(records)
}

async fn get_record<A: IoAdapter>(
    State(state): State<Arc<DiagState<A>>>,
    AxumPath(id): AxumPath<u64>,
) -> axum::response::Response {
    let core = state.core.lock().expect("core mutex");
    match core.find_record(RecordId(id)) {
        Some(record) => {
            let decisions: Vec<_> = core
                .decisions()
                .iter()
                .filter(|d| d.record_id == Some(id))
                .cloned()
                .collect();
            Json(serde_json::json!({
                "record": RecordView::from(record),
                "decisions": decisions,
            }))
            .into_response()
        }
        None => error_response(
            StatusCode::NOT_FOUND,
            "unknown_record",
            format!(
                "record {id} is not in the recent in-memory window; it may have been \
                 evicted or never existed — check the journal file for a definitive answer"
            ),
        ),
    }
}

async fn get_decisions<A: IoAdapter>(State(state): State<Arc<DiagState<A>>>) -> impl IntoResponse {
    let core = state.core.lock().expect("core mutex");
    let decisions: Vec<_> = core.decisions().iter().cloned().collect();
    Json(decisions)
}

async fn get_anomalies<A: IoAdapter>(State(state): State<Arc<DiagState<A>>>) -> impl IntoResponse {
    let core = state.core.lock().expect("core mutex");
    let anomalies: Vec<_> = core.anomalies().to_vec();
    Json(anomalies)
}

async fn get_journal<A: IoAdapter>(State(state): State<Arc<DiagState<A>>>) -> impl IntoResponse {
    Json(journal::read_tail(&state.journal_path, 50))
}

// --- demo IO endpoints -----------------------------------------------------

#[derive(Deserialize)]
pub struct SubmitRequest {
    pub user_data: u64,
    pub op: OpKind,
    #[serde(default)]
    pub timeout_ms: u64,
}

#[derive(Serialize)]
struct SubmitResponse {
    record_id: u64,
    slot: u16,
    generation: u32,
    note: &'static str,
}

async fn post_submit<A: IoAdapter>(
    State(state): State<Arc<DiagState<A>>>,
    Json(req): Json<SubmitRequest>,
) -> axum::response::Response {
    let mut core = state.core.lock().expect("core mutex");
    let fingerprint = redact_user_data(req.user_data);
    match core.submit(Submission {
        user_data: req.user_data,
        op: req.op,
        timeout_ms: req.timeout_ms,
    }) {
        Ok(accepted) => Json(SubmitResponse {
            record_id: accepted.record_id.0,
            slot: accepted.handle.slot,
            generation: accepted.handle.generation.0,
            note: "accepted; completion will be journaled exactly once",
        })
        .into_response(),
        Err(SubmitError::QueueFull {
            in_flight,
            capacity,
        }) => error_response(
            StatusCode::TOO_MANY_REQUESTS,
            "queue_full",
            format!(
                "backpressure: {in_flight}/{capacity} slots in flight; \
                 retry after a completion (user_data {fingerprint})"
            ),
        ),
        Err(SubmitError::NoBuffersAvailable { in_flight }) => error_response(
            StatusCode::TOO_MANY_REQUESTS,
            "no_buffers_available",
            format!(
                "backpressure: buffer pool exhausted with {in_flight} in flight \
                 (user_data {fingerprint})"
            ),
        ),
    }
}

#[derive(Deserialize)]
pub struct CancelRequest {
    pub slot: u16,
    pub generation: u32,
}

#[derive(Serialize)]
struct CancelResponse {
    record_id: u64,
    ack: &'static str,
    note: &'static str,
}

async fn post_cancel<A: IoAdapter>(
    State(state): State<Arc<DiagState<A>>>,
    Json(req): Json<CancelRequest>,
) -> axum::response::Response {
    let mut core = state.core.lock().expect("core mutex");
    let handle = Handle {
        slot: req.slot,
        generation: crate::model::Generation(req.generation),
    };
    match core.cancel(handle) {
        Ok(accepted) => Json(CancelResponse {
            record_id: accepted.record_id.0,
            ack: match accepted.ack {
                CancelAck::WillCancel => "will_cancel",
                CancelAck::Unsupported => "unsupported",
                CancelAck::UnknownSlot => "unknown_slot",
            },
            note: "cancel request accepted; the IO may still complete — \
                   watch the record for the terminal outcome",
        })
        .into_response(),
        Err(CancelError::UnknownHandle { slot }) => error_response(
            StatusCode::NOT_FOUND,
            "unknown_handle",
            format!("slot {slot} does not exist"),
        ),
        Err(CancelError::StaleGeneration {
            slot,
            got,
            current,
        }) => error_response(
            StatusCode::CONFLICT,
            "stale_generation",
            format!(
                "slot {slot} handle carries generation {got} but current is {current}; \
                 the original record already finalized"
            ),
        ),
        Err(CancelError::NotInFlight { slot }) => error_response(
            StatusCode::CONFLICT,
            "not_in_flight",
            format!("slot {slot} exists but no record is in flight on it"),
        ),
        Err(CancelError::AlreadyRequested { record_id }) => error_response(
            StatusCode::CONFLICT,
            "already_requested",
            format!("cancel already requested for record {}", record_id.0),
        ),
    }
}

/// Build the diagnostics/demo router.
pub fn router<A: IoAdapter>(state: Arc<DiagState<A>>) -> Router {
    Router::new()
        .route("/diag/state", get(get_state::<A>))
        .route("/diag/records", get(get_records::<A>))
        .route("/diag/records/:id", get(get_record::<A>))
        .route("/diag/decisions", get(get_decisions::<A>))
        .route("/diag/anomalies", get(get_anomalies::<A>))
        .route("/diag/journal", get(get_journal::<A>))
        .route("/io/submit", post(post_submit::<A>))
        .route("/io/cancel", post(post_cancel::<A>))
        .with_state(state)
}
