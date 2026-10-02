//! Axum HTTP service: run lifecycle, streaming batch ingestion, results.
//!
//! # HTTP contract
//!
//! | method & path | purpose |
//! |---|---|
//! | `GET  /health` | liveness |
//! | `POST /execute` | single-shot: schema + both relations in one JSON body |
//! | `POST /runs` | create a run (state `created`), returns partition fan-out |
//! | `POST /runs/:id/ingest/:side` | append one typed batch to `left`/`right` |
//! | `POST /runs/:id/execute` | drain partitions, finalize result fragments |
//! | `GET  /runs/:id` | state, counters and stats |
//! | `GET  /runs/:id/results?fragment=n` | one result fragment as typed rows |
//! | `GET  /runs/:id/log` | the replayable JSONL run log |
//!
//! Error bodies are uniform: `{ "error": { "kind", "code", "message",
//! "run_id"? } }`, with `kind` one of `input`, `state_conflict`,
//! `resource_exhausted`, `computation_failed` and HTTP status 400/409/507/500
//! respectively. Unknown runs return 404.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use axum::extract::{Path as AxumPath, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::{Deserialize, Serialize};

use crate::error::SetOpError;
use crate::executor::{
    ExecOutput, Executor, Partitioner, Quantifier, SetOperator, decode_result_fragment,
};
use crate::resource::Budget;
use crate::runlog::{RunRecord, RunState};
use crate::spill::SpillManager;
use crate::value::{Schema, Value, parse_row};

/// API-facing error: either a typed engine error or a 404.
enum ApiError {
    Engine(SetOpError),
    NotFound(String),
}

impl From<SetOpError> for ApiError {
    fn from(e: SetOpError) -> Self {
        ApiError::Engine(e)
    }
}

#[derive(Serialize)]
struct ErrorBody {
    error: SetOpError,
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        match self {
            ApiError::Engine(e) => {
                let status =
                    StatusCode::from_u16(e.kind.http_status()).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
                (status, Json(ErrorBody { error: e })).into_response()
            }
            ApiError::NotFound(msg) => (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({ "error": { "kind": "not_found", "code": "unknown_run", "message": msg } })),
            )
                .into_response(),
        }
    }
}

#[derive(Clone)]
struct AppState {
    inner: Arc<Mutex<HashMap<String, RunCtx>>>,
    root: std::path::PathBuf,
    default_budget: Budget,
}

struct RunCtx {
    record: RunRecord,
    schema: Schema,
    op: SetOperator,
    quantifier: Quantifier,
    budget: Budget,
    fanout: usize,
    spill: SpillManager,
    left: Partitioner,
    right: Partitioner,
    left_rows: u64,
    right_rows: u64,
    started: bool,
    output: Option<ExecOutput>,
}

/// Budget knobs accepted from clients; omitted fields take service defaults.
#[derive(Debug, Clone, Deserialize)]
pub struct BudgetRequest {
    pub memory_bytes: Option<usize>,
    pub partition_buffer_bytes: Option<usize>,
    pub partition_table_bytes: Option<usize>,
    pub spill_bytes: Option<u64>,
    pub spill_files: Option<usize>,
    pub output_buffer_rows: Option<usize>,
}

impl BudgetRequest {
    fn merge(&self, base: &Budget) -> Budget {
        Budget {
            memory_bytes: self.memory_bytes.unwrap_or(base.memory_bytes),
            partition_buffer_bytes: self
                .partition_buffer_bytes
                .unwrap_or(base.partition_buffer_bytes),
            partition_table_bytes: self
                .partition_table_bytes
                .unwrap_or(base.partition_table_bytes),
            spill_bytes: self.spill_bytes.unwrap_or(base.spill_bytes),
            spill_files: self.spill_files.unwrap_or(base.spill_files),
            output_buffer_rows: self.output_buffer_rows.unwrap_or(base.output_buffer_rows),
        }
    }
}

#[derive(Debug, Deserialize)]
struct CreateRunRequest {
    run_id: Option<String>,
    schema: Schema,
    op: SetOperator,
    quantifier: Quantifier,
    budget: Option<BudgetRequest>,
}

#[derive(Debug, Serialize)]
struct CreateRunResponse {
    run_id: String,
    state: RunState,
    fanout: usize,
    ingest: IngestEndpoints,
}

#[derive(Debug, Serialize)]
struct IngestEndpoints {
    left: String,
    right: String,
    execute: String,
    results: String,
    log: String,
}

/// A batch is an array of rows; a row is an array of column values.
#[derive(Debug, Deserialize)]
struct BatchRequest {
    rows: Vec<serde_json::Value>,
}

/// One-shot request.
#[derive(Debug, Deserialize)]
struct ExecuteRequest {
    run_id: Option<String>,
    schema: Schema,
    op: SetOperator,
    quantifier: Quantifier,
    budget: Option<BudgetRequest>,
    /// Each batch is an array of rows.
    left_batches: Vec<Vec<serde_json::Value>>,
    right_batches: Vec<Vec<serde_json::Value>>,
}

#[derive(Debug, Serialize)]
struct IngestResponse {
    run_id: String,
    side: String,
    rows_accepted: u64,
    side_total_rows: u64,
    spill_files: usize,
}

#[derive(Debug, Serialize)]
struct ExecuteResponse {
    run_id: String,
    state: RunState,
    stats: serde_json::Value,
    result_fragments: usize,
    /// Convenience: rows of fragment 0 inlined (empty if result is large; page
    /// via the results endpoint).
    rows: Vec<serde_json::Value>,
}

#[derive(Debug, Serialize)]
struct RunStatus {
    run_id: String,
    state: RunState,
    op: SetOperator,
    quantifier: Quantifier,
    fanout: usize,
    left_rows: u64,
    right_rows: u64,
    spill_files: usize,
    spill_bytes: u64,
    stats: Option<serde_json::Value>,
}

#[derive(Debug, Deserialize)]
struct FragmentQuery {
    fragment: Option<usize>,
}

/// Build the application router. `root` holds run directories and spill files.
pub fn app(root: std::path::PathBuf, default_budget: Budget) -> Router {
    default_budget
        .validate()
        .expect("default budget must be valid");
    let state = AppState {
        inner: Arc::new(Mutex::new(HashMap::new())),
        root,
        default_budget,
    };
    Router::new()
        .route("/health", get(health))
        .route("/execute", post(execute_one_shot))
        .route("/runs", post(create_run))
        .route("/runs/{id}/ingest/{side}", post(ingest))
        .route("/runs/{id}/execute", post(execute_run))
        .route("/runs/{id}", get(get_run))
        .route("/runs/{id}/results", get(get_results))
        .route("/runs/{id}/log", get(get_log))
        .with_state(state)
}

async fn health() -> Json<serde_json::Value> {
    Json(serde_json::json!({ "status": "ok" }))
}

fn streaming_fanout(budget: &Budget) -> usize {
    (budget.memory_bytes / (2 * budget.partition_buffer_bytes.max(1))).clamp(2, 4096)
}

fn new_run_id() -> String {
    use std::sync::atomic::{AtomicU64, Ordering};
    static SEQ: AtomicU64 = AtomicU64::new(0);
    let n = SEQ.fetch_add(1, Ordering::Relaxed);
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("run_{nanos:x}_{n}_{}", std::process::id())
}

fn valid_run_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 128
        && id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

/// Validate+parse every row in a JSON batch against the schema, attaching the
/// row index to any validation error.
fn parse_batch(schema: &Schema, rows: &[serde_json::Value]) -> Result<Vec<Vec<Value>>, SetOpError> {
    rows.iter()
        .enumerate()
        .map(|(i, r)| {
            parse_row(schema, r).map_err(|mut e| {
                e.message = format!("row {i}: {}", e.message);
                e
            })
        })
        .collect()
}

fn endpoints_for(id: &str) -> IngestEndpoints {
    IngestEndpoints {
        left: format!("/runs/{id}/ingest/left"),
        right: format!("/runs/{id}/ingest/right"),
        execute: format!("/runs/{id}/execute"),
        results: format!("/runs/{id}/results?fragment=0"),
        log: format!("/runs/{id}/log"),
    }
}

async fn create_run(
    State(state): State<AppState>,
    body: String,
) -> Result<Json<CreateRunResponse>, ApiError> {
    let req: CreateRunRequest = parse_json(&body)?;
    req.schema.validate()?;
    let budget = req
        .budget
        .as_ref()
        .map(|b| b.merge(&state.default_budget))
        .unwrap_or_else(|| state.default_budget.clone());
    budget.validate()?;
    let run_id = req.run_id.unwrap_or_else(new_run_id);
    if !valid_run_id(&run_id) {
        return Err(SetOpError::input(
            "bad_run_id",
            "run_id must be 1..128 chars of [A-Za-z0-9_-]",
        )
        .into());
    }

    let mut record = RunRecord::create(&state.root, &run_id)?;
    record.save_input("create.json", body.as_bytes())?;
    let spill_dir = record.dir().join("spill");
    std::fs::create_dir_all(&spill_dir)
        .map_err(|e| SetOpError::compute("spill_dir_create", e.to_string()))?;
    let fanout = streaming_fanout(&budget);
    record.event(
        "run_configured",
        Some(serde_json::json!({
            "op": format!("{:?}_{:?}", req.op, req.quantifier),
            "fanout": fanout,
            "budget": budget_json(&budget),
        })),
        Some("fan-out derived solely from budgets; same function on all inputs"),
    )?;

    let ctx = RunCtx {
        record,
        schema: req.schema,
        op: req.op,
        quantifier: req.quantifier,
        budget: budget.clone(),
        fanout,
        spill: SpillManager::new(spill_dir, budget),
        left: Partitioner::new(fanout),
        right: Partitioner::new(fanout),
        left_rows: 0,
        right_rows: 0,
        started: false,
        output: None,
    };
    let resp = CreateRunResponse {
        run_id: run_id.clone(),
        state: RunState::Created,
        fanout,
        ingest: endpoints_for(&run_id),
    };
    {
        let mut runs = state.inner.lock().expect("runs lock poisoned");
        if runs.contains_key(&run_id) {
            return Err(SetOpError::state("run_exists", "run_id already active").into());
        }
        runs.insert(run_id, ctx);
    }
    Ok(Json(resp))
}

async fn ingest(
    State(state): State<AppState>,
    AxumPath((id, side)): AxumPath<(String, String)>,
    body: String,
) -> Result<Json<IngestResponse>, ApiError> {
    let is_left = match side.as_str() {
        "left" => true,
        "right" => false,
        other => {
            return Err(SetOpError::input(
                "bad_side",
                format!("side must be 'left' or 'right', got {other}"),
            )
            .into());
        }
    };

    let req: BatchRequest = parse_json(&body)?;
    let mut runs = state.inner.lock().expect("runs lock poisoned");
    let ctx = runs
        .get_mut(&id)
        .ok_or_else(|| ApiError::NotFound(format!("unknown run_id: {id}")))?;

    if ctx.output.is_some() {
        return Err(SetOpError::state(
            "illegal_transition",
            "cannot ingest into an already finalized run",
        )
        .into());
    }
    let rows = parse_batch(&ctx.schema, &req.rows).map_err(|e| e.with_run(&id))?;

    if !ctx.started {
        ctx.record.start()?;
        ctx.started = true;
    }
    ctx.record.save_input(
        &format!(
            "batch_{}_{}.json",
            side,
            if is_left {
                ctx.left_rows
            } else {
                ctx.right_rows
            }
        ),
        body.as_bytes(),
    )?;

    let label = if is_left { "l" } else { "r" };
    let n = rows.len() as u64;
    // One SpillManager per run carries the run budget and accumulates counters
    // across all batches, so limits are global to the run.
    let counters = {
        let RunCtx {
            spill,
            budget,
            left,
            right,
            left_rows,
            right_rows,
            ..
        } = ctx;
        if is_left {
            left.ingest_batches(std::slice::from_ref(&rows), 0, spill, budget, label)?;
            *left_rows = left_rows.checked_add(n).ok_or_else(|| {
                SetOpError::resource("count_overflow", "left row count overflowed u64")
            })?;
            *left_rows
        } else {
            right.ingest_batches(std::slice::from_ref(&rows), 0, spill, budget, label)?;
            *right_rows = right_rows.checked_add(n).ok_or_else(|| {
                SetOpError::resource("count_overflow", "right row count overflowed u64")
            })?;
            *right_rows
        };
        spill.counters()
    };
    ctx.record.event(
        "batch_ingested",
        Some(serde_json::json!({ "side": side, "rows": n, "spill_files": counters.files, "spill_bytes": counters.bytes })),
        Some("rows encoded, hash-routed, full partition buffers spilled"),
    )?;

    Ok(Json(IngestResponse {
        run_id: id,
        side: side.clone(),
        rows_accepted: n,
        side_total_rows: counters_side_rows(ctx, is_left),
        spill_files: counters.files,
    }))
}

fn counters_side_rows(ctx: &RunCtx, is_left: bool) -> u64 {
    if is_left {
        ctx.left_rows
    } else {
        ctx.right_rows
    }
}

async fn execute_run(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
) -> Result<Json<ExecuteResponse>, ApiError> {
    let mut runs = state.inner.lock().expect("runs lock poisoned");
    let ctx = runs
        .get_mut(&id)
        .ok_or_else(|| ApiError::NotFound(format!("unknown run_id: {id}")))?;
    if ctx.output.is_some() {
        return Err(SetOpError::state("illegal_transition", "run already finalized").into());
    }
    if !ctx.started {
        ctx.record.start()?;
        ctx.started = true;
    }

    let drain = drain_ctx(ctx);
    match drain {
        Ok((output, first_rows)) => {
            let stats = serde_json::to_value(&output.stats).unwrap_or(serde_json::Value::Null);
            let fragments = output.result_segments.len();
            ctx.record.succeed(&output.stats)?;
            ctx.record.event(
                "result_index",
                Some(serde_json::json!({ "fragments": fragments })),
                Some("result keyed by (canonical key, multiplicity); page via /results"),
            )?;
            ctx.output = Some(output);
            Ok(Json(ExecuteResponse {
                run_id: id,
                state: RunState::Succeeded,
                stats,
                result_fragments: fragments,
                rows: first_rows,
            }))
        }
        Err(e) => {
            let e = e.with_run(&id);
            // Best-effort failure record; surface the original error either way.
            let _ = ctx.record.fail(&e.code, &e.message);
            Err(e.into())
        }
    }
}

/// Consume a context's partitioners, run the executor, and decode fragment 0.
fn drain_ctx(ctx: &mut RunCtx) -> Result<(ExecOutput, Vec<serde_json::Value>), SetOpError> {
    let (schema, budget, fanout) = (ctx.schema.clone(), ctx.budget.clone(), ctx.fanout);
    let lsegs = std::mem::replace(&mut ctx.left, Partitioner::new(fanout))
        .finish(0, &ctx.spill, &budget, "l")?;
    let rsegs = std::mem::replace(&mut ctx.right, Partitioner::new(fanout))
        .finish(0, &ctx.spill, &budget, "r")?;

    ctx.record.event(
        "partitioning_complete",
        Some(serde_json::json!({
            "left_rows": ctx.left_rows, "right_rows": ctx.right_rows,
            "left_partitions": lsegs.len(), "right_partitions": rsegs.len(),
        })),
        Some("both relations partitioned with identical hash function"),
    )?;

    let mut executor = Executor::with_fanout(schema.clone(), budget, &ctx.spill, fanout)?;
    let out = executor.run_segments(ctx.op, ctx.quantifier, lsegs, rsegs)?;

    // Decode fragment 0 for the inline convenience field.
    let first_rows = match out.result_segments.first() {
        Some(seg) => {
            let items = ctx.spill.read_results(seg)?;
            decode_result_fragment(&schema, &items)?
                .iter()
                .map(|r| crate::value::value_to_json_row(r))
                .collect()
        }
        None => Vec::new(),
    };
    Ok((out, first_rows))
}

async fn get_run(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
) -> Result<Json<RunStatus>, ApiError> {
    let runs = state.inner.lock().expect("runs lock poisoned");
    let ctx = runs
        .get(&id)
        .ok_or_else(|| ApiError::NotFound(format!("unknown run_id: {id}")))?;
    let c = ctx.spill.counters();
    Ok(Json(RunStatus {
        run_id: id,
        state: if ctx.output.is_some() {
            RunState::Succeeded
        } else if ctx.started {
            RunState::Running
        } else {
            RunState::Created
        },
        op: ctx.op,
        quantifier: ctx.quantifier,
        fanout: ctx.fanout,
        left_rows: ctx.left_rows,
        right_rows: ctx.right_rows,
        spill_files: c.files,
        spill_bytes: c.bytes,
        stats: ctx
            .output
            .as_ref()
            .map(|o| serde_json::to_value(&o.stats).unwrap_or(serde_json::Value::Null)),
    }))
}

async fn get_results(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
    Query(q): Query<FragmentQuery>,
) -> Result<Json<serde_json::Value>, ApiError> {
    let runs = state.inner.lock().expect("runs lock poisoned");
    let ctx = runs
        .get(&id)
        .ok_or_else(|| ApiError::NotFound(format!("unknown run_id: {id}")))?;
    let out = ctx
        .output
        .as_ref()
        .ok_or_else(|| SetOpError::state("not_finalized", "run has not executed yet"))?;
    let idx = q.fragment.unwrap_or(0);
    let seg = out.result_segments.get(idx).ok_or_else(|| {
        SetOpError::input("bad_fragment", format!("fragment {idx} does not exist"))
    })?;
    let items = ctx.spill.read_results(seg)?;
    let rows = decode_result_fragment(&ctx.schema, &items)?;
    Ok(Json(serde_json::json!({
        "run_id": id,
        "fragment": idx,
        "fragment_count": out.result_segments.len(),
        "distinct_keys_in_fragment": items.len(),
        "rows": rows.iter().map(|r| crate::value::value_to_json_row(r)).collect::<Vec<_>>(),
    })))
}

async fn get_log(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
) -> Result<String, ApiError> {
    let runs = state.inner.lock().expect("runs lock poisoned");
    let ctx = runs
        .get(&id)
        .ok_or_else(|| ApiError::NotFound(format!("unknown run_id: {id}")))?;
    std::fs::read_to_string(ctx.record.dir().join("run.jsonl"))
        .map_err(|e| SetOpError::compute("log_read", e.to_string()).into())
}

async fn execute_one_shot(
    State(state): State<AppState>,
    body: String,
) -> Result<Json<ExecuteResponse>, ApiError> {
    // Parse first to validate before creating any run directory.
    let req: ExecuteRequest = parse_json(&body)?;
    req.schema.validate()?;
    let budget = req
        .budget
        .as_ref()
        .map(|b| b.merge(&state.default_budget))
        .unwrap_or_else(|| state.default_budget.clone());
    budget.validate()?;

    let run_id = req.run_id.clone().unwrap_or_else(new_run_id);
    if !valid_run_id(&run_id) {
        return Err(SetOpError::input("bad_run_id", "invalid run_id").into());
    }

    // Validate every row *before* allocating a run directory, so pure input
    // errors leave no orphan state behind.
    let parse_all =
        |batches: &[Vec<serde_json::Value>]| -> Result<Vec<Vec<Vec<Value>>>, SetOpError> {
            batches
                .iter()
                .map(|batch| parse_batch(&req.schema, batch))
                .collect()
        };
    let left_batches = parse_all(&req.left_batches)?;
    let right_batches = parse_all(&req.right_batches)?;
    let left_rows = left_batches.iter().map(Vec::len).sum::<usize>() as u64;
    let right_rows = right_batches.iter().map(Vec::len).sum::<usize>() as u64;

    let mut record = RunRecord::create(&state.root, &run_id)?;
    record.save_input("request.json", body.as_bytes())?;
    let spill_dir = record.dir().join("spill");
    std::fs::create_dir_all(&spill_dir)
        .map_err(|e| SetOpError::compute("spill_dir_create", e.to_string()))?;

    record.start()?;
    record.event(
        "run_configured",
        Some(serde_json::json!({
            "op": format!("{:?}_{:?}", req.op, req.quantifier),
            "left_rows": left_rows, "right_rows": right_rows,
            "budget": budget_json(&budget),
        })),
        Some("single-shot execution"),
    )?;

    let fanout = streaming_fanout(&budget);
    let spill = SpillManager::new(spill_dir, budget.clone());
    let mut lp = Partitioner::new(fanout);
    let mut rp = Partitioner::new(fanout);
    let outcome = (|rec: &mut RunRecord| -> Result<(), SetOpError> {
        lp.ingest_batches(&left_batches, 0, &spill, &budget, "l")?;
        rp.ingest_batches(&right_batches, 0, &spill, &budget, "r")?;
        let lsegs = lp.finish(0, &spill, &budget, "l")?;
        let rsegs = rp.finish(0, &spill, &budget, "r")?;

        let mut executor =
            Executor::with_fanout(req.schema.clone(), budget.clone(), &spill, fanout)?;
        let out = executor.run_segments(req.op, req.quantifier, lsegs, rsegs)?;

        rec.succeed(&out.stats)?;
        rec.event(
            "result_index",
            Some(serde_json::json!({ "fragments": out.result_segments.len() })),
            Some("result keyed by (canonical key, multiplicity); page via /results"),
        )?;

        // Keep the finalized run inspectable through the GET endpoints.
        let finalized = RunCtx {
            record: RunRecord::open(&state.root, &run_id)?,
            schema: req.schema.clone(),
            op: req.op,
            quantifier: req.quantifier,
            budget: budget.clone(),
            fanout,
            spill,
            left: Partitioner::new(fanout),
            right: Partitioner::new(fanout),
            left_rows,
            right_rows,
            started: true,
            output: Some(out),
        };
        state
            .inner
            .lock()
            .expect("runs lock poisoned")
            .insert(run_id.clone(), finalized);
        Ok(())
    })(&mut record);

    if let Err(e) = outcome {
        let _ = record.fail(&e.code, &e.message);
        return Err(ApiError::Engine(e.with_run(&run_id)));
    }
    drop(record);

    // Response values are read back from the stored, finalized context.
    let runs = state.inner.lock().expect("runs lock poisoned");
    let ctx = runs.get(&run_id).expect("finalized run just inserted");
    let out = ctx.output.as_ref().expect("output finalized");
    let stats = serde_json::to_value(&out.stats).unwrap_or(serde_json::Value::Null);
    let rows: Vec<serde_json::Value> = match out.result_segments.first() {
        Some(seg) => {
            spill_read_first(&ctx.spill, &ctx.schema, seg).map_err(|e| e.with_run(&run_id))?
        }
        None => Vec::new(),
    };
    Ok(Json(ExecuteResponse {
        run_id,
        state: RunState::Succeeded,
        stats,
        result_fragments: out.result_segments.len(),
        rows,
    }))
}

/// Decode one result fragment into JSON rows.
fn spill_read_first(
    spill: &SpillManager,
    schema: &crate::value::Schema,
    seg: &crate::spill::Segment,
) -> Result<Vec<serde_json::Value>, SetOpError> {
    let items = spill.read_results(seg)?;
    Ok(decode_result_fragment(schema, &items)?
        .iter()
        .map(|r| crate::value::value_to_json_row(r))
        .collect())
}

// ---- small helpers ----

fn parse_json<T: serde::de::DeserializeOwned>(body: &str) -> Result<T, SetOpError> {
    serde_json::from_str(body).map_err(|e| {
        SetOpError::input(
            "invalid_json",
            format!("request body is not valid JSON: {e}"),
        )
    })
}

fn budget_json(b: &Budget) -> serde_json::Value {
    serde_json::json!({
        "memory_bytes": b.memory_bytes,
        "partition_buffer_bytes": b.partition_buffer_bytes,
        "partition_table_bytes": b.partition_table_bytes,
        "spill_bytes": b.spill_bytes,
        "spill_files": b.spill_files,
        "output_buffer_rows": b.output_buffer_rows,
    })
}
