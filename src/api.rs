//! Axum diagnostic/REST interface. Handlers are thin: resolve specs, run the
//! engine, persist the record, return the summary. All errors use the shared
//! [`ApiError`] taxonomy.

use crate::config::Config;
use crate::cost::{RunSummary, summarize};
use crate::engine::Engine;
use crate::error::ApiError;
use crate::model::DeviceModel;
use crate::sched::deadline::DeadlineScheduler;
use crate::sched::scan::ScanScheduler;
use crate::sched::Scheduler;
use crate::state::{RunRecord, RunStore};
use crate::trace::{self, OpJson, Trace};
use axum::{
    extract::{Path, State},
    http::StatusCode,
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use serde::{Deserialize, Serialize};
use std::sync::Arc;

pub struct AppState {
    pub store: RunStore,
    pub cfg: Config,
}

pub fn router(state: Arc<AppState>) -> Router {
    Router::new()
        .route("/v1/health", get(health))
        .route("/v1/version", get(version))
        .route("/v1/diagnostics/state", get(diagnostics))
        .route("/v1/runs", post(create_run).get(list_runs))
        .route("/v1/runs/compare", post(compare))
        .route("/v1/runs/:id", get(get_run))
        .route("/v1/runs/:id/events", get(get_events))
        .with_state(state)
}

// ---------- request/response types ----------

#[derive(Debug, Deserialize)]
pub struct TraceSpec {
    /// Fixture name ("tiny", "sequential", ...) or "random".
    pub name: Option<String>,
    /// Seed for name="random" (default 1).
    pub seed: Option<u64>,
    /// Request count for name="random" (default 64).
    pub count: Option<usize>,
    /// Inline operations; takes precedence over `name`.
    pub ops: Option<Vec<OpJson>>,
}

#[derive(Debug, Deserialize)]
pub struct SchedulerSpec {
    pub kind: String,
    pub read_expire_ns: Option<u64>,
    pub write_expire_ns: Option<u64>,
    pub writes_starved: Option<u32>,
    /// "up" | "down" (scan only).
    pub initial_direction: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct DeviceSpec {
    pub kind: String,
    pub seek_base_ns: Option<u64>,
    pub seek_ns_per_sector: Option<u64>,
    pub transfer_ns_per_sector: Option<u64>,
    pub fixed_latency_ns: Option<u64>,
}

#[derive(Debug, Deserialize)]
pub struct RunRequest {
    pub trace: TraceSpec,
    pub scheduler: SchedulerSpec,
    /// Defaults to the configured HDD model.
    pub device: Option<DeviceSpec>,
}

#[derive(Debug, Deserialize)]
pub struct CompareRequest {
    pub trace: TraceSpec,
    pub device: Option<DeviceSpec>,
}

#[derive(Debug, Serialize)]
pub struct RunCreated {
    pub run_id: String,
    pub summary: RunSummary,
}

#[derive(Debug, Serialize)]
pub struct CompareResponse {
    pub deadline_run_id: String,
    pub scan_run_id: String,
    pub deadline: RunSummary,
    pub scan: RunSummary,
    pub comparison: Comparison,
}

#[derive(Debug, Serialize)]
pub struct Comparison {
    /// scan - deadline, per metric (positive = scan is larger/slower).
    pub makespan_delta_ns: i64,
    pub total_seek_distance_delta_sectors: i64,
    pub deadline_miss_delta: i64,
    pub notes: Vec<String>,
}

// ---------- resolution helpers ----------

fn resolve_trace(spec: &TraceSpec, cfg: &Config) -> Result<Trace, ApiError> {
    if let Some(ops) = &spec.ops {
        return trace::parse_ops("inline", ops.clone());
    }
    let name = spec.name.as_deref().unwrap_or("");
    if name.is_empty() {
        return Err(ApiError::Validation(
            "trace spec needs either `ops` or a fixture `name`".into(),
        ));
    }
    if name == "random" {
        return Ok(trace::random_trace(
            spec.seed.unwrap_or(1),
            spec.count.unwrap_or(64),
        ));
    }
    trace::load_fixture(std::path::Path::new(&cfg.fixtures_dir), name)
}

fn resolve_scheduler(spec: &SchedulerSpec, cfg: &Config) -> Result<Box<dyn Scheduler>, ApiError> {
    match spec.kind.as_str() {
        "deadline" => Ok(Box::new(DeadlineScheduler::new(
            spec.read_expire_ns.unwrap_or(cfg.deadline.read_expire_ns),
            spec.write_expire_ns.unwrap_or(cfg.deadline.write_expire_ns),
            spec.writes_starved.unwrap_or(cfg.deadline.writes_starved),
        ))),
        "scan" => {
            let up = match spec.initial_direction.as_deref() {
                None => cfg.scan_initial_up,
                Some("up") => true,
                Some("down") => false,
                Some(other) => {
                    return Err(ApiError::Validation(format!(
                        "initial_direction must be \"up\" or \"down\", got {other:?}"
                    )))
                }
            };
            Ok(Box::new(ScanScheduler::new(up)))
        }
        other => Err(ApiError::Validation(format!(
            "unknown scheduler kind {other:?}; expected \"deadline\" or \"scan\""
        ))),
    }
}

fn resolve_device(spec: &Option<DeviceSpec>, cfg: &Config) -> Result<DeviceModel, ApiError> {
    let spec = match spec {
        None => return cfg.default_device("hdd").ok_or_else(|| {
            ApiError::Internal("default hdd device missing from config".into())
        }),
        Some(s) => s,
    };
    let base = cfg
        .default_device(&spec.kind)
        .ok_or_else(|| {
            ApiError::Validation(format!(
                "unknown device kind {:?}; expected \"hdd\" or \"ssd_like\"",
                spec.kind
            ))
        })?;
    Ok(match (base, spec) {
        (
            DeviceModel::Hdd {
                seek_base_ns,
                seek_ns_per_sector,
                transfer_ns_per_sector,
            },
            s,
        ) => DeviceModel::Hdd {
            seek_base_ns: s.seek_base_ns.unwrap_or(seek_base_ns),
            seek_ns_per_sector: s.seek_ns_per_sector.unwrap_or(seek_ns_per_sector),
            transfer_ns_per_sector: s.transfer_ns_per_sector.unwrap_or(transfer_ns_per_sector),
        },
        (
            DeviceModel::SsdLike {
                fixed_latency_ns,
                transfer_ns_per_sector,
            },
            s,
        ) => DeviceModel::SsdLike {
            fixed_latency_ns: s.fixed_latency_ns.unwrap_or(fixed_latency_ns),
            transfer_ns_per_sector: s.transfer_ns_per_sector.unwrap_or(transfer_ns_per_sector),
        },
    })
}

fn execute(
    state: &AppState,
    tr: &Trace,
    scheduler: Box<dyn Scheduler>,
    device: DeviceModel,
) -> Result<RunRecord, ApiError> {
    let scheduler_name = scheduler.name();
    let engine = Engine::new(device.clone(), scheduler, state.cfg.head_start_sector);
    let output = engine.run(tr);
    let run_id = state.store.next_run_id();
    let (summary, events) = summarize(run_id, &tr.name, scheduler_name, &device, output);
    let record = RunRecord { summary, events };
    state.store.save(record.clone())?;
    Ok(record)
}

// ---------- handlers ----------

async fn health() -> impl IntoResponse {
    Json(serde_json::json!({"status": "ok"}))
}

async fn version() -> impl IntoResponse {
    Json(serde_json::json!({
        "version": env!("CARGO_PKG_VERSION"),
        "event_schema": 1,
    }))
}

async fn diagnostics(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    let runs = state.store.list();
    Json(serde_json::json!({
        "runs_stored": runs.len(),
        "run_ids": runs,
        "journal_dir": format!("{}/runs", state.cfg.data_dir),
        "config": state.cfg,
        "device_caveats": [
            DeviceModel::Hdd{seek_base_ns:0,seek_ns_per_sector:0,transfer_ns_per_sector:0}.caveat(),
            DeviceModel::SsdLike{fixed_latency_ns:0,transfer_ns_per_sector:0}.caveat(),
        ],
    }))
}

async fn list_runs(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    Json(serde_json::json!({"runs": state.store.list()}))
}

async fn create_run(
    State(state): State<Arc<AppState>>,
    Json(req): Json<RunRequest>,
) -> Result<impl IntoResponse, ApiError> {
    let tr = resolve_trace(&req.trace, &state.cfg)?;
    let scheduler = resolve_scheduler(&req.scheduler, &state.cfg)?;
    let device = resolve_device(&req.device, &state.cfg)?;
    let record = execute(&state, &tr, scheduler, device)?;
    Ok((
        StatusCode::CREATED,
        Json(RunCreated {
            run_id: record.summary.run_id.clone(),
            summary: record.summary,
        }),
    ))
}

async fn compare(
    State(state): State<Arc<AppState>>,
    Json(req): Json<CompareRequest>,
) -> Result<impl IntoResponse, ApiError> {
    let tr = resolve_trace(&req.trace, &state.cfg)?;
    let device = resolve_device(&req.device, &state.cfg)?;
    let dl_spec = SchedulerSpec {
        kind: "deadline".into(),
        read_expire_ns: None,
        write_expire_ns: None,
        writes_starved: None,
        initial_direction: None,
    };
    let sc_spec = SchedulerSpec {
        kind: "scan".into(),
        read_expire_ns: None,
        write_expire_ns: None,
        writes_starved: None,
        initial_direction: None,
    };
    let dl = execute(
        &state,
        &tr,
        resolve_scheduler(&dl_spec, &state.cfg)?,
        device.clone(),
    )?;
    let sc = execute(&state, &tr, resolve_scheduler(&sc_spec, &state.cfg)?, device)?;
    let comparison = Comparison {
        makespan_delta_ns: sc.summary.makespan_ns as i64 - dl.summary.makespan_ns as i64,
        total_seek_distance_delta_sectors: sc.summary.total_seek_distance_sectors as i64
            - dl.summary.total_seek_distance_sectors as i64,
        deadline_miss_delta: sc.summary.deadline_misses.len() as i64
            - dl.summary.deadline_misses.len() as i64,
        notes: vec![
            format!(
                "deadline missed {} request deadline(s), scan missed {}",
                dl.summary.deadline_misses.len(),
                sc.summary.deadline_misses.len()
            ),
            format!(
                "scan travelled {} sectors of seek distance, deadline travelled {}",
                sc.summary.total_seek_distance_sectors,
                dl.summary.total_seek_distance_sectors
            ),
            format!(
                "scan makespan {} ns, deadline makespan {} ns",
                sc.summary.makespan_ns, dl.summary.makespan_ns
            ),
        ],
    };
    Ok(Json(CompareResponse {
        deadline_run_id: dl.summary.run_id.clone(),
        scan_run_id: sc.summary.run_id.clone(),
        deadline: dl.summary,
        scan: sc.summary,
        comparison,
    }))
}

async fn get_run(
    State(state): State<Arc<AppState>>,
    Path(id): Path<String>,
) -> Result<impl IntoResponse, ApiError> {
    Ok(Json(state.store.get(&id)?.summary))
}

async fn get_events(
    State(state): State<Arc<AppState>>,
    Path(id): Path<String>,
) -> Result<impl IntoResponse, ApiError> {
    let record = state.store.get(&id)?;
    Ok(Json(serde_json::json!({
        "run_id": id,
        "events": record.events,
    })))
}
