//! Axum HTTP entrypoint: validates requests, runs an operator, and returns a
//! stable JSON envelope. Error kinds map to distinct HTTP statuses so clients
//! can distinguish bad input (400), state conflicts (409), resource
//! exhaustion (422/507) and compute failures (500).

mod dto;

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use axum::extract::{Path as AxumPath, State};
use axum::http::StatusCode;
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::Serialize;

use crate::batch::json::{batch_to_json, build_batch};
use crate::batch::{Schema, TypedBatch, read_typed_csv_batches};
use crate::error::{ErrorKind, InputCode, ResourceCode, SetOpsError, StateCode};
use crate::operator::{ExecutionMode, Query, SetOp, execute};
use crate::resource::ResourceLimits;
use crate::runlog::RunEvent;

pub use dto::{QueryRequest, Source};

#[derive(Clone)]
pub struct AppState {
    fixture_root: PathBuf,
    spill_root: PathBuf,
    runs: std::sync::Arc<Mutex<HashMap<String, StoredRun>>>,
}

#[derive(Serialize)]
struct StoredRun {
    events: Vec<RunEvent>,
}

impl AppState {
    pub fn new(fixture_root: PathBuf, spill_root: PathBuf) -> Self {
        Self {
            fixture_root,
            spill_root,
            runs: std::sync::Arc::new(Mutex::new(HashMap::new())),
        }
    }
}

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/v1/query", post(run_query))
        .route("/v1/runs/:run_id/events", get(run_events))
        .with_state(state)
}

pub async fn serve(state: AppState, addr: SocketAddr) -> std::io::Result<()> {
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, router(state)).await
}

async fn health() -> Json<serde_json::Value> {
    Json(serde_json::json!({"status": "ok", "service": "set_ops"}))
}

fn status_for(kind: &ErrorKind) -> StatusCode {
    match kind {
        ErrorKind::Input(_) => StatusCode::BAD_REQUEST,
        ErrorKind::StateConflict(StateCode::RunIdConflict | StateCode::UnknownRun) => {
            StatusCode::CONFLICT
        }
        ErrorKind::StateConflict(StateCode::SpillCorrupted) => StatusCode::CONFLICT,
        ErrorKind::ResourceExhausted(ResourceCode::SpillIo) => StatusCode::INSUFFICIENT_STORAGE,
        ErrorKind::ResourceExhausted(_) => StatusCode::UNPROCESSABLE_ENTITY,
        ErrorKind::Compute => StatusCode::INTERNAL_SERVER_ERROR,
    }
}

fn err_response(e: SetOpsError) -> (StatusCode, Json<serde_json::Value>) {
    let status = status_for(&e.kind);
    let (kind, code) = kind_pair(&e.kind);
    let body = serde_json::json!({
        "status": "error",
        "error": {
            "kind": kind,
            "code": code,
            "message": e.message,
            "context": e.context,
        }
    });
    (status, Json(body))
}

fn kind_pair(kind: &ErrorKind) -> (&'static str, Option<serde_json::Value>) {
    match kind {
        ErrorKind::Input(c) => ("input", Some(serde_json::json!(c))),
        ErrorKind::StateConflict(c) => ("state_conflict", Some(serde_json::json!(c))),
        ErrorKind::ResourceExhausted(c) => ("resource_exhausted", Some(serde_json::json!(c))),
        ErrorKind::Compute => ("compute", None),
    }
}

async fn run_query(
    State(state): State<AppState>,
    body: axum::body::Bytes,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let req = match serde_json::from_slice::<QueryRequest>(&body) {
        Ok(x) => x,
        Err(e) => {
            return Err(err_response(SetOpsError::input(
                InputCode::InvalidRequest,
                format!("malformed JSON request: {e}"),
            )));
        }
    };
    let result = run_query_inner(&state, req).await;
    match result {
        Ok(envelope) => Ok(Json(envelope)),
        Err(e) => Err(err_response(e)),
    }
}

async fn run_query_inner(
    state: &AppState,
    req: QueryRequest,
) -> Result<serde_json::Value, SetOpsError> {
    let op = SetOp::parse(&req.op).map_err(|m| SetOpsError::input(InputCode::InvalidRequest, m))?;
    let qualifier = req.qualifier();
    let mode = req.mode();
    let limits = req.limits();
    limits.validate()?;

    let schema = Schema::parse_header(&req.schema)?;

    let batch_rows = req.batch_rows.unwrap_or(64).max(1);
    let mut left = load_source(state, &schema, &req.left, batch_rows)?;
    let mut right = load_source(state, &schema, &req.right, batch_rows)?;
    for (side, batches) in [("left", &left), ("right", &right)] {
        for b in batches {
            schema
                .compatible_with(&b.schema)
                .map_err(|e| e.ctx(|c| c.at(format!("{side} input"))))?;
        }
    }
    // A rowless side still contributes one zero-row batch carrying the schema
    // so the operator can infer it and handle empty inputs correctly.
    if left.is_empty() {
        left.push(build_batch(&schema, &[])?);
    }
    if right.is_empty() {
        right.push(build_batch(&schema, &[])?);
    }

    let run_id = req.run_id.clone();
    let spill_dir: Option<&Path> = match mode {
        ExecutionMode::InMemory => None,
        _ => Some(&state.spill_root),
    };

    let out = execute(
        Query::new(op, qualifier, left, right),
        limits,
        mode,
        spill_dir,
        run_id,
    )?;

    let rows: Vec<Vec<serde_json::Value>> = out.batches.iter().flat_map(batch_to_json).collect();
    let replay = out.log.replay_summary();
    let events = out.log.events().to_vec();
    let run_id = out.stats.run_id.clone();

    state
        .runs
        .lock()
        .expect("run lock")
        .insert(run_id.clone(), StoredRun { events });

    Ok(serde_json::json!({
        "status": "ok",
        "run_id": run_id,
        "schema": state_schema_json(&schema),
        "stats": out.stats,
        "replay": replay,
        "rows": rows,
    }))
}

fn state_schema_json(schema: &Schema) -> Vec<serde_json::Value> {
    schema
        .fields()
        .iter()
        .map(|f| serde_json::json!({"name": f.name, "type": format!("{:?}", f.ty).to_ascii_lowercase()}))
        .collect()
}

async fn run_events(
    State(state): State<AppState>,
    AxumPath(run_id): AxumPath<String>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let runs = state.runs.lock().expect("run lock");
    match runs.get(&run_id) {
        Some(r) => Ok(Json(
            serde_json::json!({"run_id": run_id, "events": r.events}),
        )),
        None => Err(err_response(SetOpsError::state(
            StateCode::UnknownRun,
            format!("unknown or expired run '{run_id}'"),
        ))),
    }
}

fn load_source(
    state: &AppState,
    schema: &Schema,
    source: &Source,
    batch_rows: usize,
) -> Result<Vec<TypedBatch>, SetOpsError> {
    match source {
        Source::Fixture {
            fixture,
            batch_rows: per,
        } => {
            let path = resolve_fixture(&state.fixture_root, fixture)?;
            read_typed_csv_batches(path, per.unwrap_or(batch_rows))
        }
        Source::Rows {
            rows,
            batch_rows: per,
        } => {
            let chunk = per.unwrap_or(batch_rows).max(1);
            rows.chunks(chunk)
                .map(|slice| build_batch(schema, slice))
                .collect()
        }
    }
}

/// Fixture paths must stay inside the configured fixture root. The relative
/// path is normalised *lexically* first, so `..` escape is rejected even when
/// the target (or an intermediate directory) does not exist. Existing files
/// are additionally canonicalised, defeating symlink escapes.
fn resolve_fixture(root: &Path, rel: &str) -> Result<PathBuf, SetOpsError> {
    if rel.is_empty() {
        return Err(SetOpsError::input(
            InputCode::InvalidRequest,
            "empty fixture path",
        ));
    }
    if Path::new(rel).is_absolute() {
        return Err(SetOpsError::input(
            InputCode::FixturePathDenied,
            "absolute fixture paths are not allowed",
        ));
    }

    let joined = root.join(rel);
    let lexical = normalize_lexically(&joined);
    let lexical_root = normalize_lexically(root);
    if !lexical.starts_with(&lexical_root) {
        return Err(SetOpsError::input(
            InputCode::FixturePathDenied,
            format!("fixture '{rel}' escapes the fixture root"),
        ));
    }
    if !lexical.exists() {
        return Err(SetOpsError::input(
            InputCode::NotFound,
            format!("fixture '{rel}' does not exist"),
        ));
    }
    let canonical_root = std::fs::canonicalize(root).map_err(|e| {
        SetOpsError::input(
            InputCode::NotFound,
            format!("fixture root {} unavailable: {e}", root.display()),
        )
    })?;
    let canonical = std::fs::canonicalize(&lexical)
        .map_err(|e| SetOpsError::input(InputCode::NotFound, format!("fixture '{rel}': {e}")))?;
    if !canonical.starts_with(&canonical_root) {
        return Err(SetOpsError::input(
            InputCode::FixturePathDenied,
            format!("fixture '{rel}' resolves outside the fixture root"),
        ));
    }
    Ok(canonical)
}

/// Resolve `.` and `..` components without touching the filesystem.
fn normalize_lexically(path: &Path) -> PathBuf {
    use std::path::Component;
    let mut out = PathBuf::new();
    for comp in path.components() {
        match comp {
            Component::ParentDir => {
                out.pop();
            }
            Component::CurDir => {}
            other => out.push(other.as_os_str()),
        }
    }
    out
}

/// Re-export for the CLI, which builds the same envelope offline.
pub fn json_limits(limits: &ResourceLimits) -> serde_json::Value {
    serde_json::to_value(limits).unwrap_or(serde_json::Value::Null)
}
