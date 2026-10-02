//! Axum HTTP 接口：回放、诊断与状态查询。
//!
//! 路由（均返回统一信封 `{success,data,error,request_id}`）：
//!
//! | 方法 | 路径 | 职责 |
//! |------|------|------|
//! POST | /access            | 处理单次访问 `{page,kind}` |
//! POST | /replay            | 顺序回放 JSON 轨迹，逐步返回结果 |
//! POST | /resize            | 动态调整容量 `{capacity}` |
//! GET  | /stats             | 容量/列表长度/命中统计 |
//! GET  | /lists             | T1/T2/B1/B2 的 LRU→MRU 成员 |
//! GET  | /page/{id}         | 单页驻留/脏/幽灵状态（不含内容） |
//! GET  | /diagnostics       | 决策记录（支持 request_id 过滤） |
//! GET  | /health            | 存活探针 |
//!
//! 所有响应与日志均不含页内容；载荷相关展示经脱敏处理。

use std::sync::Mutex;

use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    routing::{get, post},
    Json, Router,
};
use serde::{Deserialize, Serialize};

use crate::diagnostics::redact_payload;
use crate::engine::{Engine, EngineError};
use crate::types::{Access, AccessKind, CacheStats, PageId};

#[derive(Debug, Serialize)]
pub struct Envelope<T: Serialize> {
    pub success: bool,
    pub request_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<T>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<ErrorBody>,
}

#[derive(Debug, Serialize)]
pub struct ErrorBody {
    pub code: String,
    pub message: String,
}

impl<T: Serialize> Envelope<T> {
    pub fn ok(request_id: String, data: T) -> Self {
        Self {
            success: true,
            request_id,
            data: Some(data),
            error: None,
        }
    }

    pub fn err(request_id: String, code: &str, message: String) -> Self {
        Self {
            success: false,
            request_id,
            data: None,
            error: Some(ErrorBody {
                code: code.to_string(),
                message,
            }),
        }
    }
}

#[derive(Clone)]
pub struct AppState {
    pub engine: std::sync::Arc<Mutex<Engine>>,
}

pub fn router(engine: std::sync::Arc<Mutex<Engine>>) -> Router {
    let state = AppState { engine };
    Router::new()
        .route("/health", get(health))
        .route("/access", post(access))
        .route("/replay", post(replay))
        .route("/resize", post(resize))
        .route("/stats", get(stats))
        .route("/lists", get(lists))
        .route("/page/{id}", get(page_status))
        .route("/diagnostics", get(diagnostics))
        .with_state(state)
}

async fn health() -> Json<serde_json::Value> {
    Json(serde_json::json!({ "status": "ok" }))
}

#[derive(Debug, Deserialize)]
pub struct AccessBody {
    pub page: u64,
    pub kind: AccessKind,
}

async fn access(
    State(s): State<AppState>,
    headers: axum::http::HeaderMap,
    Json(body): Json<AccessBody>,
) -> (StatusCode, Json<Envelope<serde_json::Value>>) {
    let explicit = request_id_from_headers(&headers);
    let mut engine = s.engine.lock().unwrap();
    let access = Access {
        page: PageId(body.page),
        kind: body.kind,
    };
    match engine.access(access, explicit) {
        Ok(report) => (
            StatusCode::OK,
            Json(Envelope::ok(
                report.request_id.clone(),
                serde_json::to_value(&report).unwrap(),
            )),
        ),
        Err(e) => engine_error_status(&e),
    }
}

#[derive(Debug, Deserialize)]
pub struct ReplayBody {
    pub accesses: Vec<AccessBody>,
}

#[derive(Debug, Serialize)]
pub struct ReplayStep {
    pub index: usize,
    pub request_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub outcome: Option<crate::types::AccessOutcome>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<ErrorBody>,
    pub stats_after: CacheStats,
}

async fn replay(
    State(s): State<AppState>,
    headers: axum::http::HeaderMap,
    Json(body): Json<ReplayBody>,
) -> Json<Envelope<Vec<ReplayStep>>> {
    let explicit = request_id_from_headers(&headers);
    let mut engine = s.engine.lock().unwrap();
    let mut steps = Vec::with_capacity(body.accesses.len());
    let mut aborted = false;

    for (i, a) in body.accesses.iter().enumerate() {
        if aborted {
            break;
        }
        let access = Access {
            page: PageId(a.page),
            kind: a.kind,
        };
        // 首个请求沿用外部 request_id；后续自动生成，保证可逐条追溯。
        let rid = if i == 0 { explicit.clone() } else { None };
        match engine.access(access, rid) {
            Ok(report) => steps.push(ReplayStep {
                index: i,
                request_id: report.request_id,
                outcome: Some(report.outcome),
                error: None,
                stats_after: engine.stats(),
            }),
            Err(e) => {
                let (code, rid2, msg) = error_parts(&e);
                steps.push(ReplayStep {
                    index: i,
                    request_id: rid2,
                    outcome: None,
                    error: Some(ErrorBody {
                        code: code.to_string(),
                        message: msg,
                    }),
                    stats_after: engine.stats(),
                });
                aborted = true;
            }
        }
    }

    let rid = steps
        .first()
        .map(|s| s.request_id.clone())
        .unwrap_or_else(|| "replay-empty".to_string());
    Json(Envelope::ok(rid, steps))
}

#[derive(Debug, Deserialize)]
pub struct ResizeBody {
    pub capacity: usize,
}

async fn resize(
    State(s): State<AppState>,
    headers: axum::http::HeaderMap,
    Json(body): Json<ResizeBody>,
) -> (StatusCode, Json<Envelope<serde_json::Value>>) {
    let explicit = request_id_from_headers(&headers);
    let mut engine = s.engine.lock().unwrap();
    match engine.resize(body.capacity, explicit) {
        Ok(report) => (
            StatusCode::OK,
            Json(Envelope::ok(
                report.request_id.clone(),
                serde_json::to_value(&report).unwrap(),
            )),
        ),
        Err(e) => engine_error_status(&e),
    }
}

async fn stats(State(s): State<AppState>) -> Json<serde_json::Value> {
    let engine = s.engine.lock().unwrap();
    let stats = engine.stats();
    let dirty: Vec<u64> = engine.dirty_pages().iter().map(|p| p.as_u64()).collect();
    Json(serde_json::json!({
        "success": true,
        "data": { "stats": stats, "dirty_pages": dirty }
    }))
}

async fn lists(State(s): State<AppState>) -> Json<serde_json::Value> {
    let engine = s.engine.lock().unwrap();
    Json(serde_json::json!({
        "success": true,
        "data": engine.cache().pages_lru_to_mru()
    }))
}

async fn page_status(State(s): State<AppState>, Path(id): Path<u64>) -> Json<serde_json::Value> {
    let engine = s.engine.lock().unwrap();
    let status = engine.page_status(PageId(id));
    Json(serde_json::to_value(&status).unwrap_or_else(
        |_| serde_json::json!({ "success": false, "error": "serialization failed" }),
    ))
}

#[derive(Debug, Deserialize)]
pub struct DiagQuery {
    #[serde(default)]
    pub limit: Option<usize>,
    #[serde(default)]
    pub request_id: Option<String>,
}

async fn diagnostics(
    State(s): State<AppState>,
    Query(q): Query<DiagQuery>,
) -> Json<serde_json::Value> {
    let engine = s.engine.lock().unwrap();
    let records: Vec<_> = match &q.request_id {
        Some(rid) => engine.diagnostics().for_request(rid),
        None => engine.diagnostics().recent(q.limit.unwrap_or(50)),
    };
    Json(serde_json::json!({
        "success": true,
        "data": records,
        "note": "payloads are never logged; example redaction: ".to_owned()
            + &redact_payload(b"sensitive-bytes")
    }))
}

// ---- 辅助 -----------------------------------------------------------------

fn request_id_from_headers(headers: &axum::http::HeaderMap) -> Option<String> {
    headers
        .get("x-request-id")
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string())
}

fn engine_error_status(e: &EngineError) -> (StatusCode, Json<Envelope<serde_json::Value>>) {
    let (code, rid, msg) = error_parts(e);
    // 回写/装入失败是“后端依赖故障”，用 502；语义拒绝仍为 200（见诊断）。
    (StatusCode::BAD_GATEWAY, Json(Envelope::err(rid, code, msg)))
}

fn error_parts(e: &EngineError) -> (&'static str, String, String) {
    match e {
        EngineError::WriteBack {
            page,
            kind,
            attempts,
            request_id,
        } => (
            "writeback_failed",
            request_id.clone(),
            format!("{page} write-back {kind} after {attempts} attempt(s)"),
        ),
        EngineError::Fetch {
            page,
            kind,
            request_id,
        } => (
            "fetch_failed",
            request_id.clone(),
            format!("{page} fetch {kind}"),
        ),
        EngineError::ResizeWriteBack {
            page,
            kind,
            attempts,
        } => (
            "resize_writeback_failed",
            "resize".to_string(),
            format!("{page} write-back {kind} after {attempts} attempt(s); capacity unchanged"),
        ),
    }
}
