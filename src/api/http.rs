//! Axum HTTP boundary. This is the only layer that knows about HTTP status
//! codes; failures arrive as categorized [`JoinError`]s and are mapped
//! deterministically. Every request produces a replay record carrying a run id.

use std::sync::Arc;

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde_json::json;

use crate::api::dto::{
    self, CursorDto, ErrorBody, ErrorEnvelope, JoinRequest, SessionCreateRequest,
};
use crate::error::{ErrorCategory, ErrorCode, JoinError};
use crate::operator::Checkpoint;
use crate::replay::{
    BatchFingerprint, BudgetSnapshot, ErrorSnapshot, OutcomeKind, ReplayLogger, ReplayRecord, RunId,
};
use crate::resource::Truncation;
use crate::state::{Cursor, SessionMeta, SessionRegistry};
use crate::validation::{prepare, validate_budget};

#[derive(Clone)]
pub struct AppState {
    pub registry: Arc<SessionRegistry>,
    pub replay: Arc<ReplayLogger>,
}

impl AppState {
    pub fn new(session_capacity: usize, replay: ReplayLogger) -> Self {
        Self {
            registry: Arc::new(SessionRegistry::new(session_capacity)),
            replay: Arc::new(replay),
        }
    }
}

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/healthz", get(healthz))
        .route("/join", post(join_one_shot))
        .route("/sessions", post(create_session))
        .route("/sessions/:id/continue", post(continue_session))
        .route("/runs/:run_id", get(get_run))
        .with_state(state)
}

async fn healthz() -> Json<serde_json::Value> {
    Json(json!({ "status": "ok" }))
}

// ---------------------------------------------------------------------------
// One-shot join
// ---------------------------------------------------------------------------

async fn join_one_shot(
    State(state): State<AppState>,
    Json(req): Json<JoinRequest>,
) -> Result<Json<dto::JoinResponse>, ApiError> {
    let run_id = RunId::generate();
    let plan: crate::operator::JoinPlan = req.plan.into();
    let budget: crate::resource::Budget = req.budget.into();

    // Decode first so success/failure records can both carry fingerprints.
    let decoded = decode_inputs(&req.left, &req.right);
    let outcome = decoded.and_then(|(left, right)| {
        validate_budget(&budget)?;
        let prepared = prepare(&plan, &left, &right, &budget)?;
        let page = prepared.run_page(&budget, Checkpoint::start());
        if !page.finished {
            return Err(JoinError::resource(
                ErrorCode::BudgetExceeded,
                format!(
                    "join exceeds one-page budget ({})",
                    truncation_str(page.truncation)
                ),
            ));
        }
        Ok((
            page,
            BatchFingerprint::of(&left),
            BatchFingerprint::of(&right),
        ))
    });

    let _record_id = match &outcome {
        Ok((page, lfp, rfp)) => state.replay.record(ReplayRecord {
            run_id: run_id.to_string(),
            outcome: OutcomeKind::Completed,
            plan,
            budget: BudgetSnapshot::from(budget),
            left: lfp.clone(),
            right: rfp.clone(),
            checkpoint: page.next.into(),
            stats: page.stats,
            truncation: Some(page.truncation),
            error: None,
            rationale: rationale_for(Some(page.truncation), None),
        }),
        Err(e) => state.replay.record(failure_record(
            run_id.to_string(),
            plan,
            budget,
            fingerprint_of(&req.left),
            fingerprint_of(&req.right),
            e,
        )),
    };

    let (page, _, _) = outcome?;
    Ok(Json(dto::JoinResponse {
        run_id: run_id.to_string(),
        pairs: page
            .pairs
            .iter()
            .map(|p| dto::PairDto {
                left_row: p.left_row,
                right_row: p.right_row,
            })
            .collect(),
        stats: dto::StatsDto {
            emitted: page.stats.emitted,
            candidate_accesses: page.stats.candidate_accesses,
            rows_driven: page.stats.rows_driven,
        },
        truncation: truncation_str(page.truncation).to_string(),
        finished: page.finished,
    }))
}

fn decode_inputs(
    left: &dto::BatchDto,
    right: &dto::BatchDto,
) -> Result<(crate::types::TypedBatch, crate::types::TypedBatch), JoinError> {
    Ok((
        dto::decode_batch(left.clone())?,
        dto::decode_batch(right.clone())?,
    ))
}

fn fingerprint_of(batch: &dto::BatchDto) -> Option<BatchFingerprint> {
    dto::decode_batch(batch.clone())
        .ok()
        .map(|b| BatchFingerprint::of(&b))
}

// ---------------------------------------------------------------------------
// Paged sessions
// ---------------------------------------------------------------------------

async fn create_session(
    State(state): State<AppState>,
    Json(req): Json<SessionCreateRequest>,
) -> Result<Json<dto::SessionResponse>, ApiError> {
    let run_id = RunId::generate();
    let session_id = format!("s-{run_id}");
    let plan: crate::operator::JoinPlan = req.plan.into();
    let budget: crate::resource::Budget = req.budget.into();

    let decoded = decode_inputs(&req.left, &req.right);
    let outcome = decoded.and_then(|(left, right)| {
        validate_budget(&budget)?;
        let prepared = prepare(&plan, &left, &right, &budget)?;
        let meta = SessionMeta {
            plan,
            budget,
            left: BatchFingerprint::of(&left),
            right: BatchFingerprint::of(&right),
        };
        state.registry.create(session_id.clone(), prepared, meta)?;
        let page = state.registry.first_page(&session_id)?;
        Ok((
            page,
            BatchFingerprint::of(&left),
            BatchFingerprint::of(&right),
        ))
    });

    match outcome {
        Ok((page, lfp, rfp)) => {
            state.replay.record(ReplayRecord {
                run_id: run_id.to_string(),
                outcome: if page.finished {
                    OutcomeKind::Completed
                } else {
                    OutcomeKind::Truncated
                },
                plan,
                budget: BudgetSnapshot::from(budget),
                left: lfp,
                right: rfp,
                checkpoint: page.next.into(),
                stats: page.stats,
                truncation: Some(page.truncation),
                error: None,
                rationale: rationale_for(Some(page.truncation), None),
            });
            Ok(Json(session_body(run_id.to_string(), session_id, 0, page)))
        }
        Err(e) => {
            state.replay.record(failure_record(
                run_id.to_string(),
                plan,
                budget,
                fingerprint_of(&req.left),
                fingerprint_of(&req.right),
                &e,
            ));
            Err(ApiError::from(e))
        }
    }
}

async fn continue_session(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<dto::ContinueRequest>,
) -> Result<Json<dto::SessionResponse>, ApiError> {
    let run_id = RunId::generate();
    let cursor = cursor_from_dto(&body.cursor)?;
    let page = state.registry.next_page(&id, &cursor)?;
    // The session is live (next_page succeeded), so metadata is present and
    // the continuation record stays fully reconstructable.
    let meta = state
        .registry
        .meta(&id)
        .ok_or_else(|| JoinError::compute("session vanished between page and metadata lookup"))?;
    state.replay.record(ReplayRecord {
        run_id: run_id.to_string(),
        outcome: if page.finished {
            OutcomeKind::Completed
        } else {
            OutcomeKind::Truncated
        },
        plan: meta.plan,
        budget: BudgetSnapshot::from(meta.budget),
        left: meta.left,
        right: meta.right,
        checkpoint: page.next.into(),
        stats: page.stats,
        truncation: Some(page.truncation),
        error: None,
        rationale: format!(
            "continuation page {} ended {:?}",
            cursor.page_index + 1,
            page.truncation
        ),
    });
    Ok(Json(session_body(
        run_id.to_string(),
        id,
        cursor.page_index,
        page,
    )))
}

async fn get_run(
    State(state): State<AppState>,
    Path(run_id): Path<String>,
) -> Result<Json<ReplayRecord>, ApiError> {
    state
        .replay
        .get(&run_id)
        .map(Json)
        .ok_or_else(|| JoinError::state(ErrorCode::UnknownRun, format!("run '{run_id}' not found")))
        .map_err(ApiError::from)
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn session_body(
    run_id: String,
    session_id: String,
    page_index: usize,
    page: crate::operator::JoinPage,
) -> dto::SessionResponse {
    let next_cursor = if page.finished {
        None
    } else {
        Some(CursorDto {
            session_id: session_id.clone(),
            page_index: page_index + 1,
            checkpoint: page.next.into(),
            truncation: truncation_str(page.truncation).to_string(),
        })
    };
    dto::SessionResponse {
        run_id,
        session_id,
        page_index,
        pairs: page
            .pairs
            .iter()
            .map(|p| dto::PairDto {
                left_row: p.left_row,
                right_row: p.right_row,
            })
            .collect(),
        stats: dto::StatsDto {
            emitted: page.stats.emitted,
            candidate_accesses: page.stats.candidate_accesses,
            rows_driven: page.stats.rows_driven,
        },
        truncation: truncation_str(page.truncation).to_string(),
        finished: page.finished,
        next_cursor,
    }
}

fn cursor_from_dto(dto: &CursorDto) -> Result<Cursor, JoinError> {
    if dto.session_id.is_empty() {
        return Err(JoinError::input(
            ErrorCode::InvalidPayload,
            "cursor session_id is empty",
        ));
    }
    Ok(Cursor {
        session_id: dto.session_id.clone(),
        page_index: dto.page_index,
        checkpoint: Checkpoint::from_parts(
            dto.checkpoint.dpos,
            dto.checkpoint.act_pos,
            dto.checkpoint.probe_pos,
            dto.checkpoint.probe_hi,
        ),
        truncation: truncation_parse(&dto.truncation)?,
    })
}

fn truncation_str(t: Truncation) -> &'static str {
    match t {
        Truncation::Complete => "complete",
        Truncation::OutputLimit => "output_limit",
        Truncation::CandidateLimit => "candidate_limit",
    }
}

fn truncation_parse(s: &str) -> Result<Truncation, JoinError> {
    match s {
        "complete" => Ok(Truncation::Complete),
        "output_limit" => Ok(Truncation::OutputLimit),
        "candidate_limit" => Ok(Truncation::CandidateLimit),
        other => Err(JoinError::input(
            ErrorCode::InvalidPayload,
            format!("unknown truncation '{other}'"),
        )),
    }
}

fn rationale_for(truncation: Option<Truncation>, error: Option<&JoinError>) -> String {
    if let Some(e) = error {
        return format!(
            "{} failure: {}",
            category_str(e.category()),
            e.code.as_str()
        );
    }
    match truncation {
        Some(Truncation::Complete) | None => "all matching pairs emitted within budget".to_string(),
        Some(Truncation::OutputLimit) => {
            "stopped at max_output boundary; cursor resumes the scan".to_string()
        }
        Some(Truncation::CandidateLimit) => {
            "stopped at max_candidate_accesses boundary; cursor resumes the scan".to_string()
        }
    }
}

fn category_str(c: ErrorCategory) -> &'static str {
    match c {
        ErrorCategory::Input => "input",
        ErrorCategory::State => "state",
        ErrorCategory::Resource => "resource",
        ErrorCategory::Compute => "compute",
    }
}

#[allow(clippy::too_many_arguments)]
fn failure_record(
    run_id: String,
    plan: crate::operator::JoinPlan,
    budget: crate::resource::Budget,
    left: Option<BatchFingerprint>,
    right: Option<BatchFingerprint>,
    e: &JoinError,
) -> ReplayRecord {
    let empty = BatchFingerprint {
        rows: 0,
        columns: vec![],
    };
    ReplayRecord {
        run_id,
        outcome: OutcomeKind::Failed,
        plan,
        budget: BudgetSnapshot::from(budget),
        left: left.unwrap_or_else(|| empty.clone()),
        right: right.unwrap_or(empty),
        checkpoint: crate::replay::CheckpointSnapshot {
            dpos: 0,
            act_pos: 0,
            probe_pos: 0,
            probe_hi: 0,
        },
        stats: crate::resource::JoinStats::default(),
        truncation: None,
        error: Some(ErrorSnapshot::from_error(e)),
        rationale: rationale_for(None, Some(e)),
    }
}

// ---------------------------------------------------------------------------
// Error mapping
// ---------------------------------------------------------------------------

struct ApiError {
    inner: JoinError,
}

impl From<JoinError> for ApiError {
    fn from(inner: JoinError) -> Self {
        Self { inner }
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let e = self.inner;
        let status = match e.category() {
            ErrorCategory::Input => StatusCode::BAD_REQUEST,
            ErrorCategory::State => match e.code {
                ErrorCode::UnknownRun => StatusCode::NOT_FOUND,
                _ => StatusCode::CONFLICT,
            },
            ErrorCategory::Resource => match e.code {
                ErrorCode::SessionLimitReached => StatusCode::SERVICE_UNAVAILABLE,
                _ => StatusCode::PAYLOAD_TOO_LARGE,
            },
            ErrorCategory::Compute => StatusCode::INTERNAL_SERVER_ERROR,
        };
        let body = ErrorEnvelope {
            error: ErrorBody {
                code: e.code.as_str().to_string(),
                category: category_str(e.category()).to_string(),
                message: e.message,
            },
        };
        (status, Json(body)).into_response()
    }
}
