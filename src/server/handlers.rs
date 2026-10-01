//! Axum handlers: validation entry point, execution, cancellation, resume.

use std::cmp::min;
use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::{Arc, Mutex};

use axum::extract::{ConnectInfo, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde_json::json;

use crate::batch::{Batch, Field};
use crate::diagnostics::{new_request_id, Decision, KeyState};
use crate::error::{Error, ErrorKind};
use crate::exec::cancel::Token;
use crate::exec::{Engine, EngineConfig, ExecStats};
use crate::fixtures;
use crate::plan::Plan;
use crate::server::dto::{
    cell_to_json, groups_to_json, CancelRequest, ErrorBody, QueryData, QueryRequest, ResumeRequest,
    StatsDto,
};

/// Server bind settings.
#[derive(Debug, Clone)]
pub struct ServerConfig {
    pub bind: SocketAddr,
}

/// Shared application state.
#[derive(Clone)]
pub struct AppState {
    engine: Arc<Engine>,
    engine_config: Arc<EngineConfig>,
    /// query_id → live cancellation token.  Tokens are removed when the query
    /// finishes, but spill state on disk survives for resume.
    running: Arc<Mutex<HashMap<String, Token>>>,
}

impl AppState {
    pub fn new(config: EngineConfig) -> Self {
        Self {
            engine: Arc::new(Engine::new(config.clone())),
            engine_config: Arc::new(config),
            running: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    fn register(&self, query_id: &str, token: Token) {
        self.running
            .lock()
            .expect("running lock is never poisoned")
            .insert(query_id.to_string(), token);
    }

    fn unregister(&self, query_id: &str) {
        self.running
            .lock()
            .expect("running lock is never poisoned")
            .remove(query_id);
    }

    fn cancel(&self, query_id: &str) -> bool {
        self.running
            .lock()
            .expect("running lock is never poisoned")
            .get(query_id)
            .map(|t| {
                t.cancel();
                true
            })
            .unwrap_or(false)
    }
}

pub fn build_router(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/fixtures", get(list_fixtures))
        .route("/query", post(run_query))
        .route("/query/resume", post(resume_query))
        .route("/query/cancel", post(cancel_query))
        .with_state(state)
}

async fn health() -> Json<serde_json::Value> {
    Json(json!({"status": "ok", "service": "groupagg"}))
}

async fn list_fixtures() -> Json<serde_json::Value> {
    let list: Vec<_> = [
        ("even_sample", "hand-computed even percentile sample"),
        ("all_nulls", "group with entirely nullable measurements"),
        ("mode_tie", "equal-frequency mode tie, smallest wins"),
        ("large_repeat_group", "1000 repeated tag values vs 3"),
        (
            "skewed_groups",
            "one heavy long-string group + many singletons",
        ),
    ]
    .into_iter()
    .map(|(name, desc)| json!({"name": name, "description": desc}))
    .collect();
    Json(json!({"fixtures": list}))
}

#[allow(clippy::needless_pass_by_value)]
async fn run_query(
    State(state): State<AppState>,
    headers: HeaderMap,
    connect: ConnectInfo<SocketAddr>,
    Json(req): Json<QueryRequest>,
) -> Response {
    let request_id = request_id_from_headers(&headers);
    let query_id = req
        .query_id
        .clone()
        .unwrap_or_else(|| format!("q-{}", &request_id[4..min(14, request_id.len())]));
    let _remote = connect.0;

    if !is_safe_id(&query_id) {
        return reject(
            &request_id,
            &query_id,
            "query_id must match [A-Za-z0-9_-]{1,64}",
            ErrorKind::InvalidRequest,
        );
    }

    // ---- Decode + validate everything before the engine runs. -------------
    let plan = match Plan::from_json(&req.plan) {
        Ok(p) => p,
        Err(e) => return map_validation_error(&request_id, &query_id, e),
    };

    let loaded = match load_input(&req) {
        Ok(v) => v,
        Err(e) => return map_validation_error(&request_id, &query_id, e),
    };

    if let Err(e) = Engine::validate_schema_pub(&loaded.schema, &plan) {
        return map_validation_error(&request_id, &query_id, e);
    }

    let cancel_token = match req.cancel_after_merge_checks {
        Some(checks) => Token::with_auto_merge_cancel(checks),
        None => Token::new(),
    };
    execute_blocking(
        state,
        request_id,
        query_id,
        plan,
        loaded.schema,
        loaded.batches,
        None,
        cancel_token,
    )
    .await
}

#[allow(clippy::needless_pass_by_value)]
async fn resume_query(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(req): Json<ResumeRequest>,
) -> Response {
    let request_id = request_id_from_headers(&headers);
    let query_id = req.query_id;
    let plan = match Plan::from_json(&req.plan) {
        Ok(p) => p,
        Err(e) => return map_validation_error(&request_id, &query_id, e),
    };
    let token = state.engine.resume_token(&query_id, &plan);
    execute_blocking(
        state,
        request_id,
        query_id.clone(),
        plan,
        Vec::new(),
        Vec::new(),
        Some(token),
        Token::new(),
    )
    .await
}

#[allow(clippy::needless_pass_by_value)]
async fn cancel_query(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(req): Json<CancelRequest>,
) -> Response {
    let request_id = request_id_from_headers(&headers);
    let found = state.cancel(&req.query_id);
    let body = json!({
        "status": "ok",
        "request_id": request_id,
        "query_id": req.query_id,
        "cancelled": found,
    });
    Json(body).into_response()
}

// ---------------------------------------------------------------------------
// Shared execution plumbing
// ---------------------------------------------------------------------------

struct LoadedInput {
    schema: Vec<Field>,
    batches: Vec<Batch>,
}

fn load_input(req: &QueryRequest) -> Result<LoadedInput, Error> {
    if let Some(name) = &req.fixture {
        return load_fixture(name);
    }
    let schema_dtos = req
        .schema
        .as_ref()
        .ok_or_else(|| Error::invalid_request("either 'fixture' or 'schema' must be provided"))?;
    let schema: Result<Vec<Field>, Error> = schema_dtos.iter().map(|f| f.to_field()).collect();
    let schema = schema?;

    let raw_batches: &[serde_json::Map<String, serde_json::Value>] =
        match (&req.batches, &req.columns) {
            (Some(batches), _) => batches,
            (None, Some(columns)) => std::slice::from_ref(columns),
            (None, None) => {
                return Err(Error::invalid_request(
                    "either 'batches' or 'columns' must be provided",
                ));
            }
        };
    let batches = raw_batches
        .iter()
        .map(|cols| Batch::from_json(&schema, cols))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(LoadedInput { schema, batches })
}

fn load_fixture(name: &str) -> Result<LoadedInput, Error> {
    let fixture = match name {
        "even_sample" => fixtures::even_sample(),
        "all_nulls" => fixtures::all_nulls(),
        "mode_tie" => fixtures::mode_tie(),
        "large_repeat_group" => fixtures::large_repeat_group(),
        "skewed_groups" => fixtures::skewed_groups(2_000, 200),
        other => {
            return Err(Error::invalid_request(format!(
                "unknown fixture '{other}'; see GET /fixtures"
            )))
        }
    };
    Ok(LoadedInput {
        schema: fixture.schema,
        batches: fixture.batches,
    })
}

#[allow(clippy::too_many_arguments)]
async fn execute_blocking(
    state: AppState,
    request_id: String,
    query_id: String,
    plan: Plan,
    schema: Vec<Field>,
    batches: Vec<Batch>,
    resume: Option<crate::exec::spill::ResumeToken>,
    token: Token,
) -> Response {
    state.register(&query_id, token.clone());
    let engine = state.engine.clone();
    let exec_query_id = query_id.clone();
    let exec_plan = plan.clone();

    let outcome = tokio::task::spawn_blocking(move || {
        if let Some(resume_token) = resume {
            engine.resume(&resume_token, &exec_plan, &token)
        } else {
            engine.execute(&exec_query_id, &schema, &batches, &exec_plan, &token)
        }
    })
    .await
    .expect("execution task is never cancelled by the runtime");

    state.unregister(&query_id);

    match outcome {
        Ok(result) => accepted_response(&state, &request_id, &query_id, result, plan),
        Err(e) => map_execution_error(&state, &request_id, &query_id, e),
    }
}

fn accepted_response(
    state: &AppState,
    request_id: &str,
    query_id: &str,
    result: crate::exec::QueryResult,
    plan: Plan,
) -> Response {
    let group_names = plan.group_by.clone();
    let aliases: Vec<String> = plan.aggregations.iter().map(|a| a.alias.clone()).collect();
    let groups = groups_to_json(&result.groups, &group_names, &aliases);
    let decision = Decision::accepted(
        request_id,
        query_id,
        "plan validated before execution; quantiles in range and schema compatible",
        key_state_from_stats(state, &result.stats, true, false),
    );
    decision.trace();
    let data = QueryData {
        query_id: query_id.to_string(),
        groups,
        stats: StatsDto {
            ingested_rows: result.stats.ingested_rows,
            spill_runs: result.stats.spill_runs,
            peak_memory_bytes: result.stats.peak_memory_bytes,
            groups_emitted: result.stats.groups_emitted,
        },
        resume_token: None,
    };
    let envelope = json!({
        "status": "ok",
        "request_id": request_id,
        "data": data,
        "decision": decision,
    });
    (StatusCode::OK, Json(envelope)).into_response()
}

fn map_execution_error(state: &AppState, request_id: &str, query_id: &str, err: Error) -> Response {
    match err.kind {
        ErrorKind::Cancelled => {
            // Durable, complete spill state exists: the request could not be
            // decided now, but POST /query/resume deterministically finishes it.
            let stats = state.engine.manifest_stats(query_id);
            let key_state = KeyState {
                ingested_rows: stats.as_ref().map(|m| m.ingested_rows).unwrap_or(0),
                spill_runs: stats.as_ref().map(|m| m.runs.len()).unwrap_or(0),
                peak_memory_bytes: stats.as_ref().map(|m| m.peak_memory_bytes).unwrap_or(0),
                memory_budget_bytes: state.engine_config.memory_budget_bytes,
                groups_seen: 0,
                spill_complete: stats.as_ref().map(|m| m.complete).unwrap_or(false),
                resumable: true,
            };
            let decision = Decision::undetermined(
                request_id,
                query_id,
                "cancelled during merge; sorted runs are durable and the merge is resumable",
                err.kind.as_str().to_string(),
                key_state,
            );
            decision.trace();
            let resume_token = json!({
                "query_id": query_id,
                "plan_hash": stats.as_ref().map(|m| m.plan_hash.clone()).unwrap_or_default(),
            });
            let body = ErrorBody {
                status: "undetermined",
                request_id: request_id.to_string(),
                query_id: json!(query_id),
                error_kind: err.kind.as_str().to_string(),
                message: err.message,
                decision: serde_json::to_value(&decision).unwrap(),
            };
            // Attach the resume token alongside the envelope.
            let mut value = serde_json::to_value(&body).unwrap();
            value["resume_token"] = resume_token;
            (
                StatusCode::from_u16(err.kind.http_status()).unwrap(),
                Json(value),
            )
                .into_response()
        }
        ErrorKind::BudgetExceeded | ErrorKind::SpillIo => {
            let decision = Decision::undetermined(
                request_id,
                query_id,
                "execution could not complete within engine resources",
                err.kind.as_str().to_string(),
                KeyState {
                    memory_budget_bytes: state.engine_config.memory_budget_bytes,
                    ..KeyState::default()
                },
            );
            decision.trace();
            error_response(err, decision)
        }
        // InvalidResume is 409; InvalidQuantile/InvalidRequest/ParseError and
        // Unsupported are client errors and the request is simply rejected.
        _ => {
            let decision = Decision::rejected(
                request_id,
                query_id,
                format!("rejected during execution: {}", err.message),
                err.kind.as_str().to_string(),
            );
            decision.trace();
            error_response(err, decision)
        }
    }
}

fn map_validation_error(request_id: &str, query_id: &str, err: Error) -> Response {
    let status = StatusCode::from_u16(err.kind.http_status()).unwrap_or(StatusCode::BAD_REQUEST);
    let decision = Decision::rejected(
        request_id,
        query_id,
        format!("rejected before execution: {}", err.message),
        err.kind.as_str().to_string(),
    );
    decision.trace();
    let body = ErrorBody {
        status: "error",
        request_id: request_id.to_string(),
        query_id: json!(query_id),
        error_kind: err.kind.as_str().to_string(),
        message: err.message,
        decision: serde_json::to_value(&decision).unwrap(),
    };
    (status, Json(body)).into_response()
}

fn reject(request_id: &str, query_id: &str, message: &str, kind: ErrorKind) -> Response {
    map_validation_error(request_id, query_id, Error::new(kind, message))
}

fn error_response(err: Error, decision: Decision) -> Response {
    let status =
        StatusCode::from_u16(err.kind.http_status()).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    let body = ErrorBody {
        status: "error",
        request_id: decision.request_id.clone(),
        query_id: json!(decision.query_id),
        error_kind: err.kind.as_str().to_string(),
        message: err.message,
        decision: serde_json::to_value(&decision).unwrap(),
    };
    (status, Json(body)).into_response()
}

fn key_state_from_stats(
    state: &AppState,
    stats: &ExecStats,
    complete: bool,
    resumable: bool,
) -> KeyState {
    KeyState {
        ingested_rows: stats.ingested_rows,
        spill_runs: stats.spill_runs,
        peak_memory_bytes: stats.peak_memory_bytes,
        memory_budget_bytes: state.engine_config.memory_budget_bytes,
        groups_seen: stats.groups_emitted,
        spill_complete: complete,
        resumable,
    }
}

fn request_id_from_headers(headers: &HeaderMap) -> String {
    headers
        .get("x-request-id")
        .and_then(|v| v.to_str().ok())
        .filter(|s| is_safe_id(s) && s.len() <= 64)
        .map(str::to_string)
        .unwrap_or_else(new_request_id)
}

fn is_safe_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 64
        && id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

// Keep `cell_to_json` used by future diagnostic endpoints; silence unused
// import warnings if the helper is only referenced in tests.
#[allow(dead_code)]
fn ensure_cell_json_used(c: &crate::exec::cells::Cell) -> serde_json::Value {
    cell_to_json(c)
}
