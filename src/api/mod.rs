//! HTTP 诊断接口（Axum）。
//!
//! 路由总览见 [`router`]。所有 JSON 响应使用统一信封：
//! - 成功：`{"status":"ok","data": ...}`
//! - 失败：`{"status":"error","category":...,"code":...,"message":...}`
//!
//! 翻译的页故障**不是**错误：HTTP 200，`data.outcome = "page_fault"`，
//! 负载内含类型化故障与走表轨迹。

use crate::config::Config;
use crate::error::DomainError;
use crate::persistence::PersistInfo;
use crate::store::translate::TranslateOutcome;
use crate::store::Lab;
use crate::types::{MapRequest, PageSize, Permissions, TranslateRequest};
use axum::extract::{Path as AxumPath, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::Deserialize;
use serde_json::json;
use std::sync::{Arc, Mutex};

/// 共享应用状态。
#[derive(Clone)]
pub struct AppState {
    pub lab: Arc<Mutex<Lab>>,
    /// 默认快照路径（来自 MMU_SNAPSHOT_PATH）。
    pub snapshot_path: String,
}

impl AppState {
    pub fn new(lab: Lab, snapshot_path: String) -> Self {
        Self {
            lab: Arc::new(Mutex::new(lab)),
            snapshot_path,
        }
    }
}

/// 构造完整路由（测试直接用，不绑端口）。
pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/", get(service_info))
        .route("/health", get(health))
        .route("/machine", get(machine_constants))
        // 地址空间
        .route("/asids", post(create_asid).get(list_asids))
        .route("/asids/{asid}", get(get_asid).delete(destroy_asid))
        // 映射
        .route("/maps", post(create_map).get(list_maps))
        .route("/maps/unmap", post(unmap_map))
        .route("/maps/protect", post(protect_map))
        // 翻译
        .route("/translate", post(translate))
        // TLB
        .route("/tlb", get(tlb_state))
        .route("/tlb/invalidate", post(invalidate_tlb))
        // 物理帧
        .route("/frames", get(frames_state))
        // 持久化 / 重置
        .route("/snapshot/save", post(save_snapshot))
        .route("/snapshot/load", post(load_snapshot))
        .route("/reset", post(reset))
        // 诊断事件
        .route("/events", get(list_events))
        .route("/events/{run_id}", get(get_event))
        .with_state(state)
}

// ---------------------------------------------------------------------------
// 响应信封与错误映射
// ---------------------------------------------------------------------------

fn ok<T: serde::Serialize>(data: T) -> Response {
    (StatusCode::OK, Json(json!({"status": "ok", "data": data}))).into_response()
}

/// API 层错误：域错误 + 请求体解析错误。
struct ApiError {
    status: StatusCode,
    category: String,
    code: String,
    message: String,
}

impl From<DomainError> for ApiError {
    fn from(e: DomainError) -> Self {
        let status =
            StatusCode::from_u16(e.http_status()).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
        ApiError {
            status,
            category: e.category().into(),
            code: e.code().into(),
            message: e.detail(),
        }
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (
            self.status,
            Json(json!({
                "status": "error",
                "category": self.category,
                "code": self.code,
                "message": self.message,
            })),
        )
            .into_response()
    }
}

fn parse_error(e: impl std::fmt::Display) -> ApiError {
    ApiError {
        status: StatusCode::BAD_REQUEST,
        category: "input_error".into(),
        code: "INVALID_JSON".into(),
        message: format!("请求体解析失败：{e}"),
    }
}

fn with_lab<T>(
    state: &AppState,
    f: impl FnOnce(&mut Lab) -> Result<T, DomainError>,
) -> Result<T, ApiError> {
    let mut lab = state.lab.lock().expect("Lab mutex 中毒");
    f(&mut lab).map_err(ApiError::from)
}

// ---------------------------------------------------------------------------
// 基础信息
// ---------------------------------------------------------------------------

async fn health() -> Response {
    ok(json!({"ok": true, "service": "mmu-lab"}))
}

async fn service_info(State(s): State<AppState>) -> Response {
    let lab = s.lab.lock().unwrap();
    ok(json!({
        "service": "mmu-lab",
        "purpose": "受限多级页表与 TLB 地址翻译教学服务（合成环境，不操作真实内核页表）",
        "endpoints": [
            "GET /machine", "POST /asids", "POST /maps", "POST /translate",
            "POST /maps/unmap", "POST /maps/protect",
            "POST /tlb/invalidate", "GET /tlb", "GET /frames",
            "POST /snapshot/save", "POST /snapshot/load", "POST /reset",
            "GET /events", "GET /events/{run_id}"
        ],
        "config": lab.config(),
        "snapshot_path": s.snapshot_path,
    }))
}

async fn machine_constants() -> Response {
    ok(json!({
        "va_bits": crate::config::VA_BITS,
        "pa_bits": crate::config::PA_BITS,
        "page_size": crate::config::PAGE_SIZE,
        "large_page_size": crate::config::LARGE_PAGE_SIZE,
        "levels": crate::config::LEVELS,
        "bits_per_level": crate::config::BITS_PER_LEVEL,
        "entries_per_table": crate::config::ENTRIES_PER_TABLE,
        "max_access_len": crate::config::MAX_ACCESS_LEN,
        "pte_layout": {
            "bits": {"V": 0, "R": 1, "W": 2, "X": 3, "D": 4, "G": 5, "PS": 6},
            "reserved": "bits 9:7、39:32、63:40 必须为零",
            "ppn_bits": 20,
        },
        "split_example": {"va_hex": "0x00401234", "l1": 1, "l2": 1, "offset_hex": "0x234"},
    }))
}

// ---------------------------------------------------------------------------
// 地址空间
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
struct CreateAsidBody {
    #[serde(default)]
    name: Option<String>,
}

async fn create_asid(
    State(s): State<AppState>,
    body: Result<Json<CreateAsidBody>, axum::extract::rejection::JsonRejection>,
) -> Result<Response, ApiError> {
    let Json(body) = body.map_err(parse_error)?;
    let space = with_lab(&s, |l| l.create_asid(body.name))?;
    Ok(ok(json!({ "asid": space.0, "space": space.1 })))
}

async fn list_asids(State(s): State<AppState>) -> Response {
    let lab = s.lab.lock().unwrap();
    ok(json!({
        "spaces": lab.spaces().values().collect::<Vec<_>>(),
        "count": lab.spaces().len(),
    }))
}

async fn get_asid(
    State(s): State<AppState>,
    AxumPath(asid): AxumPath<u16>,
) -> Result<Response, ApiError> {
    let space = with_lab(&s, |l| l.require_space(asid).cloned())?;
    Ok(ok(space))
}

async fn destroy_asid(
    State(s): State<AppState>,
    AxumPath(asid): AxumPath<u16>,
) -> Result<Response, ApiError> {
    with_lab(&s, |l| l.destroy_asid(asid))?;
    Ok(ok(json!({"destroyed": asid})))
}

// ---------------------------------------------------------------------------
// 映射
// ---------------------------------------------------------------------------

async fn create_map(
    State(s): State<AppState>,
    body: Result<Json<MapRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Response, ApiError> {
    let Json(req) = body.map_err(parse_error)?;
    let outcome = with_lab(&s, |l| l.map(&req))?;
    Ok(ok(outcome))
}

#[derive(Debug, Deserialize)]
struct MappingsQuery {
    asid: Option<u16>,
}

async fn list_maps(State(s): State<AppState>, Query(q): Query<MappingsQuery>) -> Response {
    let lab = s.lab.lock().unwrap();
    let records: Vec<_> = lab
        .mappings()
        .values()
        .filter(|m| match q.asid {
            Some(a) => m.global || m.owner_asid == a,
            None => true,
        })
        .collect();
    ok(json!({"mappings": records, "count": records.len()}))
}

#[derive(Debug, Deserialize)]
struct UnmapBody {
    asid: u16,
    va: u64,
    #[serde(default)]
    global: bool,
    #[serde(default = "default_true")]
    invalidate: bool,
}

#[derive(Debug, Deserialize)]
struct ProtectBody {
    asid: u16,
    va: u64,
    #[serde(default)]
    global: bool,
    permissions: Permissions,
    #[serde(default = "default_true")]
    invalidate: bool,
}

fn default_true() -> bool {
    true
}

async fn unmap_map(
    State(s): State<AppState>,
    body: Result<Json<UnmapBody>, axum::extract::rejection::JsonRejection>,
) -> Result<Response, ApiError> {
    let Json(b) = body.map_err(parse_error)?;
    let out = with_lab(&s, |l| l.unmap(b.asid, b.va, b.global, b.invalidate))?;
    Ok(ok(out))
}

async fn protect_map(
    State(s): State<AppState>,
    body: Result<Json<ProtectBody>, axum::extract::rejection::JsonRejection>,
) -> Result<Response, ApiError> {
    let Json(b) = body.map_err(parse_error)?;
    let out = with_lab(&s, |l| {
        l.protect(b.asid, b.va, b.global, b.permissions, b.invalidate)
    })?;
    Ok(ok(out))
}

// ---------------------------------------------------------------------------
// 翻译
// ---------------------------------------------------------------------------

async fn translate(
    State(s): State<AppState>,
    body: Result<Json<TranslateRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Response, ApiError> {
    let Json(req) = body.map_err(parse_error)?;
    let outcome = with_lab(&s, |l| l.translate(&req))?;
    let payload = match outcome {
        TranslateOutcome::Ok(res) => json!({ "outcome": "translated", "translation": res }),
        TranslateOutcome::Fault(fault) => {
            json!({ "outcome": "page_fault", "fault": fault })
        }
    };
    Ok(ok(payload))
}

// ---------------------------------------------------------------------------
// TLB / 帧
// ---------------------------------------------------------------------------

async fn tlb_state(State(s): State<AppState>) -> Response {
    let lab = s.lab.lock().unwrap();
    ok(json!({
        "capacity": lab.tlb().capacity(),
        "len": lab.tlb().len(),
        "fills": lab.tlb().fills(),
        "invalidations": lab.tlb().invalidations(),
        "entries": lab.tlb().snapshot().entries,
    }))
}

#[derive(Debug, Deserialize)]
struct InvalidateBody {
    asid: Option<u16>,
    va: Option<u64>,
    page: Option<PageSize>,
}

async fn invalidate_tlb(
    State(s): State<AppState>,
    body: Result<Json<InvalidateBody>, axum::extract::rejection::JsonRejection>,
) -> Result<Response, ApiError> {
    let Json(b) = body.map_err(parse_error)?;
    let removed = with_lab(&s, |l| l.invalidate(b.asid, b.va, b.page))?;
    Ok(ok(json!({"removed": removed})))
}

async fn frames_state(State(s): State<AppState>) -> Response {
    let lab = s.lab.lock().unwrap();
    ok(json!({
        "total": lab.frames().total(),
        "used": lab.frames().used(),
        "available": lab.frames().available(),
        "peak_used": lab.frames().peak(),
        "frame_size": crate::config::PAGE_SIZE,
        "large_page_frames": crate::config::ENTRIES_PER_TABLE,
    }))
}

// ---------------------------------------------------------------------------
// 持久化 / 重置
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
struct PathBody {
    path: Option<String>,
}

async fn save_snapshot(
    State(s): State<AppState>,
    body: Result<Json<PathBody>, axum::extract::rejection::JsonRejection>,
) -> Result<Response, ApiError> {
    let Json(b) = body.map_err(parse_error)?;
    let path = b.path.unwrap_or_else(|| s.snapshot_path.clone());
    let info: PersistInfo = with_lab(&s, |l| l.save_to_file(&path))?;
    Ok(ok(info))
}

async fn load_snapshot(
    State(s): State<AppState>,
    body: Result<Json<PathBody>, axum::extract::rejection::JsonRejection>,
) -> Result<Response, ApiError> {
    let Json(b) = body.map_err(parse_error)?;
    let path = b.path.unwrap_or_else(|| s.snapshot_path.clone());
    let restored = Lab::load_from_file(&path)?;
    let summary = json!({
        "spaces": restored.spaces().len(),
        "mappings": restored.mappings().len(),
        "frames_available": restored.frames().available(),
    });
    *s.lab.lock().unwrap() = restored;
    Ok(ok(json!({"loaded": path, "state": summary})))
}

#[derive(Debug, Deserialize)]
struct ResetBody {
    #[serde(default)]
    config: Option<Config>,
}

async fn reset(
    State(s): State<AppState>,
    body: Result<Json<ResetBody>, axum::extract::rejection::JsonRejection>,
) -> Result<Response, ApiError> {
    let cfg = match body {
        Ok(Json(b)) => b
            .config
            .unwrap_or_else(|| s.lab.lock().unwrap().config().clone()),
        // 允许空 body。
        Err(_) => s.lab.lock().unwrap().config().clone(),
    };
    cfg.validate()
        .map_err(|m| ApiError::from(DomainError::Input(m)))?;
    *s.lab.lock().unwrap() = Lab::new(cfg.clone());
    Ok(ok(json!({"reset": true, "config": cfg})))
}

// ---------------------------------------------------------------------------
// 诊断事件
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
struct EventsQuery {
    #[serde(default = "default_event_limit")]
    limit: usize,
}

fn default_event_limit() -> usize {
    50
}

async fn list_events(State(s): State<AppState>, Query(q): Query<EventsQuery>) -> Response {
    let lab = s.lab.lock().unwrap();
    let events = lab.events().recent(q.limit.max(1));
    ok(json!({
        "stored": lab.events().len(),
        "capacity": lab.events().capacity(),
        "dropped_oldest": lab.events().dropped(),
        "events": events,
    }))
}

async fn get_event(
    State(s): State<AppState>,
    AxumPath(run_id): AxumPath<String>,
) -> Result<Response, ApiError> {
    let lab = s.lab.lock().unwrap();
    let event = lab.events().find(&run_id).cloned().ok_or_else(|| {
        DomainError::NotFound(format!("run_id {run_id} 不在事件环中（可能已被覆盖）"))
    })?;
    Ok(ok(event))
}
