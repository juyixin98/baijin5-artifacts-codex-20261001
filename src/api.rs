//! Axum HTTP 诊断/操作接口。
//!
//! 状态码契约：
//! - 翻译/访存的**页故障**是正常模拟结果：HTTP 200 + 判别式 JSON（`result=page_fault`），
//!   故障以 [`FaultKind`] 稳定字符串给出，服务不 panic；
//! - 400 `input_error`：请求体/参数非法（非规范地址等）；
//! - 409 `conflict`：状态冲突（进程不存在、大小页覆盖冲突等）；
//! - 507 `resource_exhausted`：帧池/ASID/进程数耗尽；
//! - 500 `compute_failure`：内部不一致。

use axum::{
    body::Body,
    extract::{rejection::JsonRejection, Path, Query, State},
    http::{Request, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde::Deserialize;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;

use crate::errors::{ErrorBody, PageFault, ServiceError};
use crate::machine::{Machine, MachineSnapshot};
use crate::pte::{AccessOp, Permissions};

#[derive(Clone)]
pub struct AppState {
    machine: Arc<RwLock<Machine>>,
}

impl AppState {
    pub fn new(machine: Machine) -> Self {
        AppState {
            machine: Arc::new(RwLock::new(machine)),
        }
    }

    /// 测试与 main 共用：从 Arc<RwLock> 构造。
    pub fn from_shared(machine: Arc<RwLock<Machine>>) -> Self {
        AppState { machine }
    }
}

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/", get(index))
        .route("/api/v1/info", get(info))
        .route(
            "/api/v1/processes",
            get(list_processes).post(create_process),
        )
        .route(
            "/api/v1/processes/{asid}",
            axum::routing::delete(destroy_process),
        )
        .route("/api/v1/processes/{asid}/mappings", post(map_page))
        .route("/api/v1/processes/{asid}/mappings/unmap", post(unmap_page))
        .route("/api/v1/processes/{asid}/protect", post(reprotect_page))
        .route("/api/v1/processes/{asid}/raw-pte", post(raw_write_pte))
        .route("/api/v1/processes/{asid}/walk", get(walk_diag))
        .route("/api/v1/translate", post(translate))
        .route("/api/v1/access", post(access))
        .route("/api/v1/sfence", post(sfence))
        .route("/api/v1/tlb", get(tlb_snapshot))
        .route("/api/v1/runs", get(runs_recent))
        .route("/api/v1/runs/{id}", get(run_by_id))
        .route("/api/v1/snapshot", get(snapshot))
        .route("/api/v1/snapshot/save", post(snapshot_save))
        .route("/api/v1/snapshot/load", post(snapshot_load))
        .with_state(state)
        .fallback(fallback)
}

async fn index() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "service": "mmu-teach",
        "description": "受限多级页表与 TLB 地址翻译教学服务（纯模拟，不操作真实内核页表）",
        "endpoints": [
            "GET  /api/v1/info",
            "POST /api/v1/processes",
            "GET  /api/v1/processes",
            "DELETE /api/v1/processes/{asid}",
            "POST /api/v1/processes/{asid}/mappings",
            "POST /api/v1/processes/{asid}/mappings/unmap",
            "POST /api/v1/processes/{asid}/protect",
            "POST /api/v1/processes/{asid}/raw-pte",
            "GET  /api/v1/processes/{asid}/walk?vaddr=0x..",
            "POST /api/v1/translate",
            "POST /api/v1/access",
            "POST /api/v1/sfence",
            "GET  /api/v1/tlb",
            "GET  /api/v1/runs?limit=50",
            "GET  /api/v1/runs/{id}",
            "GET  /api/v1/snapshot",
            "POST /api/v1/snapshot/save",
            "POST /api/v1/snapshot/load"
        ]
    }))
}

async fn fallback(_req: Request<Body>) -> Response {
    (
        StatusCode::NOT_FOUND,
        Json(ErrorBody {
            error: "not_found".into(),
            kind: "input_error".into(),
            message: "路径不存在".into(),
        }),
    )
        .into_response()
}

// ---------------------------------------------------------------------------
// 数字解析：JSON 数字或 "0x.."/"0o.."/十进制字符串均可。
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy)]
struct Num(u64);

impl<'de> Deserialize<'de> for Num {
    fn deserialize<D>(d: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let v = serde_json::Value::deserialize(d)?;
        match v {
            serde_json::Value::Number(n) => n
                .as_u64()
                .map(Num)
                .ok_or_else(|| serde::de::Error::custom("需要非负整数")),
            serde_json::Value::String(s) => {
                parse_num(&s).map(Num).map_err(serde::de::Error::custom)
            }
            _ => Err(serde::de::Error::custom("需要整数或数字字符串")),
        }
    }
}

fn parse_num(s: &str) -> Result<u64, String> {
    // 容忍教学写法中的下划线分隔（如 0xdead_0000、1_000_000）。
    let cleaned: String = s
        .trim()
        .chars()
        .filter(|c| *c != '_' && *c != ' ')
        .collect();
    let t = cleaned.as_str();
    let parsed = if let Some(h) = t.strip_prefix("0x").or_else(|| t.strip_prefix("0X")) {
        u64::from_str_radix(h, 16)
    } else if let Some(o) = t.strip_prefix("0o") {
        u64::from_str_radix(o, 8)
    } else if let Some(b) = t.strip_prefix("0b") {
        u64::from_str_radix(b, 2)
    } else {
        t.parse::<u64>()
    };
    parsed.map_err(|e| format!("无法把 '{s}' 解析为非负整数: {e}"))
}

// ---------------------------------------------------------------------------
// 请求体
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
struct CreateProcessReq {
    #[serde(default)]
    name: Option<String>,
}

#[derive(Debug, Deserialize)]
struct MapReq {
    vaddr: Num,
    /// 1=1GiB 2=2MiB 3=4KiB
    level: u8,
    ppn: Num,
    #[serde(default)]
    read: bool,
    #[serde(default)]
    write: bool,
    #[serde(default)]
    execute: bool,
    #[serde(default)]
    user: bool,
    #[serde(default)]
    global: bool,
}

impl MapReq {
    fn perms(&self) -> Permissions {
        Permissions {
            read: self.read,
            write: self.write,
            execute: self.execute,
            user: self.user,
            global: self.global,
        }
    }
}

#[derive(Debug, Deserialize)]
struct UnmapReq {
    vaddr: Num,
    level: u8,
}

#[derive(Debug, Deserialize)]
struct ProtectReq {
    vaddr: Num,
    #[serde(default)]
    read: bool,
    #[serde(default)]
    write: bool,
    #[serde(default)]
    execute: bool,
    #[serde(default)]
    user: bool,
    #[serde(default)]
    global: bool,
}

#[derive(Debug, Deserialize)]
struct RawPteReq {
    vaddr: Num,
    level: u8,
    value: Num,
}

#[derive(Debug, Deserialize)]
struct TranslateReq {
    asid: u16,
    vaddr: Num,
    op: AccessOp,
}

#[derive(Debug, Deserialize)]
struct AccessReq {
    asid: u16,
    vaddr: Num,
    len: Num,
    op: AccessOp,
}

#[derive(Debug, Deserialize)]
struct SfenceReq {
    #[serde(default)]
    asid: Option<u16>,
    #[serde(default)]
    vpn: Option<Num>,
}

#[derive(Debug, Deserialize)]
struct PathReq {
    path: String,
}

/// JSON 体提取失败时统一转 400（axum 的 JsonRejection 不直接作为错误返回）。
/// 错误装箱以避免大尺寸 `Result`（clippy::result_large_err）。
fn json_or_400<T>(r: Result<Json<T>, JsonRejection>) -> Result<T, Box<Response>> {
    match r {
        Ok(Json(v)) => Ok(v),
        Err(rej) => Err(Box::new(bad_request(&format!(
            "请求体 JSON 解析失败: {rej}"
        )))),
    }
}

// ---------------------------------------------------------------------------
// 处理器
// ---------------------------------------------------------------------------

async fn info(State(s): State<AppState>) -> Json<serde_json::Value> {
    let m = s.machine.read().await;
    Json(serde_json::to_value(m.info()).unwrap())
}

async fn create_process(
    State(s): State<AppState>,
    body: Result<Json<CreateProcessReq>, JsonRejection>,
) -> Response {
    let req = match json_or_400(body) {
        Ok(v) => v,
        Err(r) => return *r,
    };
    let name = req.name.unwrap_or_else(|| "anon".into());
    let m = s.machine.read().await;
    match m.create_process(name) {
        Ok(info) => (StatusCode::CREATED, Json(info)).into_response(),
        Err(e) => service_err(e),
    }
}

async fn list_processes(State(s): State<AppState>) -> Json<serde_json::Value> {
    let m = s.machine.read().await;
    Json(serde_json::json!({ "processes": m.list_processes() }))
}

async fn destroy_process(State(s): State<AppState>, Path(asid): Path<u16>) -> Response {
    let m = s.machine.read().await;
    match m.destroy_process(asid) {
        Ok(flush) => Json(serde_json::json!({"destroyed": asid, "flush": flush})).into_response(),
        Err(e) => service_err(e),
    }
}

async fn map_page(
    State(s): State<AppState>,
    Path(asid): Path<u16>,
    body: Result<Json<MapReq>, JsonRejection>,
) -> Response {
    let req = match json_or_400(body) {
        Ok(v) => v,
        Err(r) => return *r,
    };
    let m = s.machine.read().await;
    match m.map(asid, req.vaddr.0, req.level, req.ppn.0, req.perms()) {
        Ok((report, flush)) => (
            StatusCode::CREATED,
            Json(serde_json::json!({"mapping": report, "flush": flush})),
        )
            .into_response(),
        Err(e) => service_err(e),
    }
}

async fn unmap_page(
    State(s): State<AppState>,
    Path(asid): Path<u16>,
    body: Result<Json<UnmapReq>, JsonRejection>,
) -> Response {
    let req = match json_or_400(body) {
        Ok(v) => v,
        Err(r) => return *r,
    };
    let m = s.machine.read().await;
    match m.unmap(asid, req.vaddr.0, req.level) {
        Ok((report, flush)) => {
            Json(serde_json::json!({"unmapped": report, "flush": flush})).into_response()
        }
        Err(e) => service_err(e),
    }
}

async fn reprotect_page(
    State(s): State<AppState>,
    Path(asid): Path<u16>,
    body: Result<Json<ProtectReq>, JsonRejection>,
) -> Response {
    let req = match json_or_400(body) {
        Ok(v) => v,
        Err(r) => return *r,
    };
    let perms = Permissions {
        read: req.read,
        write: req.write,
        execute: req.execute,
        user: req.user,
        global: req.global,
    };
    let m = s.machine.read().await;
    match m.reprotect(asid, req.vaddr.0, perms) {
        Ok((report, flush)) => {
            Json(serde_json::json!({"reprotect": report, "flush": flush})).into_response()
        }
        Err(e) => service_err(e),
    }
}

async fn raw_write_pte(
    State(s): State<AppState>,
    Path(asid): Path<u16>,
    body: Result<Json<RawPteReq>, JsonRejection>,
) -> Response {
    let req = match json_or_400(body) {
        Ok(v) => v,
        Err(r) => return *r,
    };
    let m = s.machine.read().await;
    match m.raw_write_pte(asid, req.vaddr.0, req.level, req.value.0) {
        Ok(report) => Json(serde_json::json!({
            "warning": "夹具专用接口：不触发 TLB 失效协议",
            "raw_write": report
        }))
        .into_response(),
        Err(e) => service_err(e),
    }
}

async fn walk_diag(
    State(s): State<AppState>,
    Path(asid): Path<u16>,
    Query(q): Query<HashMap<String, String>>,
) -> Response {
    let Some(text) = q.get("vaddr") else {
        return bad_request("缺少查询参数 vaddr（十进制或 0x..）");
    };
    let Ok(vaddr) = parse_num(text) else {
        return bad_request(&format!("vaddr '{text}' 不是合法整数"));
    };
    let m = s.machine.read().await;
    match m.walk_diagnostic(asid, vaddr) {
        Ok(info) => Json(info).into_response(),
        Err(e) => service_err(e),
    }
}

async fn translate(
    State(s): State<AppState>,
    body: Result<Json<TranslateReq>, JsonRejection>,
) -> Response {
    let req = match json_or_400(body) {
        Ok(v) => v,
        Err(r) => return *r,
    };
    let m = s.machine.read().await;
    match m.translate(req.asid, req.vaddr.0, req.op) {
        Ok(t) => Json(serde_json::json!({ "result": "ok", "translation": t })).into_response(),
        Err(crate::machine::MachineTranslateError::Input(e)) => service_err(e),
        Err(crate::machine::MachineTranslateError::Fault(e)) => {
            let fault: &PageFault = &e.fault;
            Json(serde_json::json!({
                "result": "page_fault",
                "fault": fault,
                "source": e.source,
                "walk_steps": e.walk_steps
            }))
            .into_response()
        }
    }
}

async fn access(
    State(s): State<AppState>,
    body: Result<Json<AccessReq>, JsonRejection>,
) -> Response {
    let req = match json_or_400(body) {
        Ok(v) => v,
        Err(r) => return *r,
    };
    let m = s.machine.read().await;
    match m.access(req.asid, req.vaddr.0, req.len.0, req.op) {
        Ok(report) => Json(report).into_response(),
        Err(e) => service_err(e),
    }
}

async fn sfence(
    State(s): State<AppState>,
    body: Result<Json<SfenceReq>, JsonRejection>,
) -> Response {
    let req = match json_or_400(body) {
        Ok(v) => v,
        Err(r) => return *r,
    };
    let m = s.machine.read().await;
    let report = m.sfence_vma(req.asid, req.vpn.map(|n| n.0));
    Json(report).into_response()
}

async fn tlb_snapshot(State(s): State<AppState>) -> Json<serde_json::Value> {
    let m = s.machine.read().await;
    Json(m.tlb_snapshot())
}

async fn runs_recent(
    State(s): State<AppState>,
    Query(q): Query<HashMap<String, String>>,
) -> Response {
    let limit = match q.get("limit") {
        Some(t) => match t.parse::<usize>() {
            Ok(n) => n,
            Err(_) => return bad_request("limit 必须是无符号整数"),
        },
        None => 50,
    };
    let m = s.machine.read().await;
    let log = m.runlog();
    Json(serde_json::json!({
        "counters": log.counters(),
        "events": log.recent(limit)
    }))
    .into_response()
}

async fn run_by_id(State(s): State<AppState>, Path(id): Path<u64>) -> Response {
    let m = s.machine.read().await;
    match m.runlog().get(id) {
        Some(ev) => Json(ev).into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(ErrorBody {
                error: "run_not_found".into(),
                kind: "input_error".into(),
                message: format!("run_id {id} 已不在环形日志中（或从未存在）"),
            }),
        )
            .into_response(),
    }
}

async fn snapshot(State(s): State<AppState>) -> Json<MachineSnapshot> {
    let m = s.machine.read().await;
    Json(m.snapshot())
}

async fn snapshot_save(
    State(s): State<AppState>,
    body: Result<Json<PathReq>, JsonRejection>,
) -> Response {
    let req = match json_or_400(body) {
        Ok(v) => v,
        Err(r) => return *r,
    };
    let m = s.machine.read().await;
    match m.save_snapshot(std::path::Path::new(&req.path)) {
        Ok(()) => Json(serde_json::json!({"saved": req.path})).into_response(),
        Err(e) => service_err(e),
    }
}

async fn snapshot_load(
    State(s): State<AppState>,
    body: Result<Json<PathReq>, JsonRejection>,
) -> Response {
    let req = match json_or_400(body) {
        Ok(v) => v,
        Err(r) => return *r,
    };
    let data = match std::fs::read_to_string(&req.path) {
        Ok(s) => s,
        Err(e) => {
            return service_err(ServiceError::Input(format!(
                "读取快照 {} 失败: {e}",
                req.path
            )))
        }
    };
    let snap: MachineSnapshot = match serde_json::from_str(&data) {
        Ok(s) => s,
        Err(e) => return service_err(ServiceError::Input(format!("快照 JSON 非法: {e}"))),
    };
    // 沿用当前 RunLog，使运行编号连续。
    let log = {
        let m = s.machine.read().await;
        m.runlog()
    };
    match Machine::restore_snapshot(snap, log) {
        Ok(machine) => {
            *s.machine.write().await = machine;
            Json(serde_json::json!({"loaded": req.path})).into_response()
        }
        Err(e) => service_err(e),
    }
}

// ---------------------------------------------------------------------------
// 错误映射
// ---------------------------------------------------------------------------

fn service_err(e: ServiceError) -> Response {
    let status = match e {
        ServiceError::Input(_) => StatusCode::BAD_REQUEST,
        ServiceError::Conflict(_) => StatusCode::CONFLICT,
        ServiceError::Exhausted(_) => StatusCode::INSUFFICIENT_STORAGE, // 507
        ServiceError::Compute(_) => StatusCode::INTERNAL_SERVER_ERROR,
    };
    let body = ErrorBody {
        error: status
            .canonical_reason()
            .unwrap_or("error")
            .to_lowercase()
            .replace(' ', "_"),
        kind: e.kind().to_string(),
        message: e.message().to_string(),
    };
    (status, Json(body)).into_response()
}

fn bad_request(msg: &str) -> Response {
    (
        StatusCode::BAD_REQUEST,
        Json(ErrorBody {
            error: "bad_request".into(),
            kind: "input_error".into(),
            message: msg.to_string(),
        }),
    )
        .into_response()
}

/// 供 main 启动监听。
pub async fn serve(listen: &str, state: AppState) -> std::io::Result<()> {
    let app: Router = router(state);
    let listener = tokio::net::TcpListener::bind(listen).await?;
    axum::serve(listener, app).await
}
