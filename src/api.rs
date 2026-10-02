//! 诊断 HTTP 接口：提交轨迹、运行调度器对比、查询报告与事件日志。
//!
//! 错误语义（与 README 一致）：
//! - 400 malformed_request      —— 请求体无法解析
//! - 404 run_not_found          —— run_id 不存在
//! - 404 fixture_not_found      —— 夹具名不存在
//! - 422 * —— 轨迹/设备校验失败（sector_out_of_range、empty_request、
//!   duplicate_request_id、unknown_request、invalid_device_model）

use crate::config::ServerConfig;
use crate::engine::{Engine, RunOutcome};
use crate::model::{DeviceModel, ErrorCategory, ModelError, Trace};
use crate::scheduler::deadline::DeadlineScheduler;
use crate::scheduler::scan::ScanScheduler;
use crate::state::RunStore;
use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::{Deserialize, Serialize};
use std::sync::Arc;

/// 服务版本，随报告返回，便于结果溯源。
pub const SERVICE_VERSION: &str = env!("CARGO_PKG_VERSION");

pub struct AppState {
    pub config: ServerConfig,
    pub store: RunStore,
}

/// POST /v1/runs 的请求体。
#[derive(Debug, Deserialize)]
pub struct RunRequest {
    /// 内联轨迹（与 fixture_name 二选一）。
    pub trace: Option<Trace>,
    /// 夹具名（fixtures/<name>.json）。
    pub fixture_name: Option<String>,
    /// 要对比的调度器，缺省两个都跑。
    pub schedulers: Option<Vec<String>>,
    /// 可选设备模型覆盖。
    pub device: Option<DeviceModel>,
    /// 可选 deadline 参数覆盖。
    pub deadline: Option<crate::scheduler::deadline::DeadlineConfig>,
}

/// 一次对比运行的完整报告。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CompareReport {
    pub run_id: String,
    pub service_version: String,
    pub created_at_ms: u64,
    pub device: DeviceModel,
    pub results: Vec<RunOutcome>,
    /// 不确定或需调用方注意的结论，单独列出（不混进指标里）。
    pub notes: Vec<String>,
}

/// 统一错误响应体。
#[derive(Debug, Serialize)]
pub struct ApiError {
    pub error: ErrorBody,
}

#[derive(Debug, Serialize)]
pub struct ErrorBody {
    pub category: ErrorCategory,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
    /// 校验类错误可能一次报多条。
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub details: Vec<ModelError>,
}

impl ApiError {
    fn single(status: StatusCode, err: ModelError) -> (StatusCode, Json<ApiError>) {
        (
            status,
            Json(ApiError {
                error: ErrorBody {
                    category: err.category,
                    message: err.message,
                    request_id: err.request_id,
                    details: vec![],
                },
            }),
        )
    }

    fn validation(errors: Vec<ModelError>) -> (StatusCode, Json<ApiError>) {
        let first = errors.first().cloned().unwrap_or_else(|| {
            ModelError::new(
                ErrorCategory::MalformedRequest,
                "unknown validation failure",
            )
        });
        (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(ApiError {
                error: ErrorBody {
                    category: first.category,
                    message: format!("{} validation error(s)", errors.len()),
                    request_id: None,
                    details: errors,
                },
            }),
        )
    }
}

fn category_status(category: &ErrorCategory) -> StatusCode {
    match category {
        ErrorCategory::MalformedRequest => StatusCode::BAD_REQUEST,
        ErrorCategory::RunNotFound | ErrorCategory::FixtureNotFound => StatusCode::NOT_FOUND,
        _ => StatusCode::UNPROCESSABLE_ENTITY,
    }
}

pub fn router(state: Arc<AppState>) -> Router {
    Router::new()
        .route("/v1/health", get(health))
        .route("/v1/runs", post(create_run).get(list_runs))
        .route("/v1/runs/:run_id", get(get_run))
        .route("/v1/runs/:run_id/events", get(get_run_events))
        .with_state(state)
}

async fn health() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "status": "ok",
        "service_version": SERVICE_VERSION,
        "time_model": "synthetic mechanical seek model (NOT an SSD measurement)",
    }))
}

fn resolve_trace(
    req: &RunRequest,
    fixture_dir: &str,
) -> Result<Trace, (StatusCode, Json<ApiError>)> {
    match (&req.trace, &req.fixture_name) {
        (Some(trace), None) => Ok(trace.clone()),
        (None, Some(name)) => {
            // 防路径穿越：夹具名只允许字母数字、点、横线、下划线。
            if name.is_empty()
                || !name
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | '_'))
            {
                return Err(ApiError::single(
                    StatusCode::NOT_FOUND,
                    ModelError::new(ErrorCategory::FixtureNotFound, "invalid fixture name"),
                ));
            }
            let path = format!("{fixture_dir}/{name}.json");
            let text = std::fs::read_to_string(&path).map_err(|_| {
                ApiError::single(
                    StatusCode::NOT_FOUND,
                    ModelError::new(
                        ErrorCategory::FixtureNotFound,
                        format!("fixture '{name}' not found at {path}"),
                    ),
                )
            })?;
            serde_json::from_str(&text).map_err(|e| {
                ApiError::single(
                    StatusCode::BAD_REQUEST,
                    ModelError::new(
                        ErrorCategory::MalformedRequest,
                        format!("fixture '{name}' is not a valid trace: {e}"),
                    ),
                )
            })
        }
        _ => Err(ApiError::single(
            StatusCode::BAD_REQUEST,
            ModelError::new(
                ErrorCategory::MalformedRequest,
                "provide exactly one of 'trace' or 'fixture_name'",
            ),
        )),
    }
}

async fn create_run(
    State(state): State<Arc<AppState>>,
    body: Result<Json<RunRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<(StatusCode, Json<CompareReport>), (StatusCode, Json<ApiError>)> {
    let Json(req) = body.map_err(|e| {
        ApiError::single(
            StatusCode::BAD_REQUEST,
            ModelError::new(
                ErrorCategory::MalformedRequest,
                format!("invalid JSON body: {e}"),
            ),
        )
    })?;

    let trace = resolve_trace(&req, &state.config.fixture_dir)?;
    let device = req.device.clone().unwrap_or_default();
    let engine = Engine::new(device.clone())
        .map_err(|e| ApiError::single(category_status(&e.category), e))?;

    let deadline_cfg = req
        .deadline
        .clone()
        .unwrap_or_else(|| state.config.deadline.clone());
    let scheduler_names = req
        .schedulers
        .clone()
        .unwrap_or_else(|| vec!["deadline".to_string(), "scan".to_string()]);

    let mut results = Vec::new();
    let mut notes = Vec::new();
    for name in &scheduler_names {
        let mut scheduler: Box<dyn crate::scheduler::Scheduler> = match name.as_str() {
            "deadline" => Box::new(DeadlineScheduler::new(deadline_cfg.clone())),
            "scan" => Box::new(ScanScheduler::new()),
            other => {
                return Err(ApiError::single(
                    StatusCode::BAD_REQUEST,
                    ModelError::new(
                        ErrorCategory::MalformedRequest,
                        format!("unknown scheduler '{other}'; supported: deadline, scan"),
                    ),
                ));
            }
        };
        let outcome = engine
            .run(
                &trace,
                scheduler.as_mut(),
                deadline_cfg.read_expire_ms,
                deadline_cfg.write_expire_ms,
            )
            .map_err(ApiError::validation)?;
        if outcome.metrics.deadline_misses > 0 && name == "scan" {
            notes.push(format!(
                "scan missed {} deadline(s): SCAN does not consider deadlines, this is expected under load",
                outcome.metrics.deadline_misses
            ));
        }
        results.push(outcome);
    }

    let mut report = CompareReport {
        run_id: String::new(), // 由 store 分配
        service_version: SERVICE_VERSION.to_string(),
        created_at_ms: std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0),
        device,
        results,
        notes,
    };
    let run_id = state
        .store
        .save_report(&report)
        .map_err(|e| ApiError::single(StatusCode::INTERNAL_SERVER_ERROR, e))?;
    report.run_id = run_id;
    Ok((StatusCode::CREATED, Json(report)))
}

async fn list_runs(
    State(state): State<Arc<AppState>>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<ApiError>)> {
    let runs = state
        .store
        .list_runs()
        .map_err(|e| ApiError::single(StatusCode::INTERNAL_SERVER_ERROR, e))?;
    Ok(Json(serde_json::json!({ "runs": runs })))
}

async fn get_run(
    State(state): State<Arc<AppState>>,
    Path(run_id): Path<String>,
) -> Result<Json<CompareReport>, (StatusCode, Json<ApiError>)> {
    let report = state
        .store
        .load_report(&run_id)
        .map_err(|e| ApiError::single(category_status(&e.category), e))?;
    Ok(Json(report))
}

async fn get_run_events(
    State(state): State<Arc<AppState>>,
    Path(run_id): Path<String>,
) -> Result<impl IntoResponse, (StatusCode, Json<ApiError>)> {
    let path = state
        .store
        .events_path(&run_id)
        .map_err(|e| ApiError::single(category_status(&e.category), e))?;
    let text = std::fs::read_to_string(path).map_err(|e| {
        ApiError::single(
            StatusCode::INTERNAL_SERVER_ERROR,
            ModelError::new(ErrorCategory::RunNotFound, e.to_string()),
        )
    })?;
    Ok((
        StatusCode::OK,
        [(axum::http::header::CONTENT_TYPE, "application/x-ndjson")],
        text,
    ))
}
