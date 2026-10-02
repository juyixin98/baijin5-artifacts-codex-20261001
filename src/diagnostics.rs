//! 决策诊断：每一次访问/缩容/回写都产生一条带**请求标识**的记录，
//! 说明“为什么接受、拒绝或无法判定”以及当时的关键状态。
//!
//! 隐私约束：诊断中**绝不记录页内容**，只记录页标识、列表长度、p 值等
//! 元数据；如需展示载荷，必须经 [`redact_payload`] 脱敏。

use std::collections::VecDeque;

use serde::Serialize;

use crate::types::{Access, AccessKind, HitSource, PageId, WriteBackError};

/// 决策结论。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Verdict {
    /// 请求完成，缓存状态已按计划提交。
    Accepted,
    /// 请求被拒绝（缓存状态保持不变），见 [`DecisionRecord::reason_code`]。
    Rejected,
    /// 无法判定（例如配置不允许重试且错误类别未知时的保守分类）。
    Undetermined,
}

/// 拒绝/异常的机器可读原因码。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReasonCode {
    /// 容量为零：操作穿透到后端完成，但不被缓存接受。
    ZeroCapacityPassthrough,
    /// 淘汰脏页时回写失败，计划丢弃，请求页未装入。
    WritebackFailed,
    /// 从后端装入失败。
    FetchFailed,
    /// 缩容需要回写脏页但回写失败，容量保持不变。
    ResizeAborted,
}

/// 一条诊断记录（可序列化给 HTTP 诊断接口）。
#[derive(Debug, Clone, Serialize)]
pub struct DecisionRecord {
    pub seq: u64,
    pub request_id: String,
    pub op: &'static str,
    pub verdict: Verdict,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason_code: Option<ReasonCode>,
    /// 人类可读细节；不含页内容或其他敏感数据。
    pub detail: String,
    pub page: Option<u64>,
    pub kind: Option<AccessKind>,
    pub source: Option<HitSource>,
    /// 决策时（拒绝则为拒绝前）的关键状态。
    pub state: StateView,
}

/// 决策时刻的关键状态快照。
#[derive(Debug, Clone, Copy, Serialize)]
pub struct StateView {
    pub c: usize,
    pub p: usize,
    pub t1_len: usize,
    pub t2_len: usize,
    pub b1_len: usize,
    pub b2_len: usize,
    pub resident: usize,
}

/// 固定容量的诊断环形缓冲（防止长跑回放无限增长）。
#[derive(Debug)]
pub struct Diagnostics {
    entries: VecDeque<DecisionRecord>,
    capacity: usize,
}

impl Diagnostics {
    pub fn new(capacity: usize) -> Self {
        Self {
            entries: VecDeque::with_capacity(capacity.clamp(16, 1024)),
            capacity: capacity.max(16),
        }
    }

    pub fn record(&mut self, record: DecisionRecord) {
        if self.entries.len() == self.capacity {
            self.entries.pop_front();
        }
        self.entries.push_back(record);
    }

    /// 最近的记录（新→旧）。
    pub fn recent(&self, limit: usize) -> Vec<&DecisionRecord> {
        self.entries.iter().rev().take(limit).collect()
    }

    /// 按请求标识精确查找。
    pub fn for_request(&self, request_id: &str) -> Vec<&DecisionRecord> {
        self.entries
            .iter()
            .filter(|r| r.request_id == request_id)
            .collect()
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
}

/// 记录构造辅助：保证每次决策记录的字段完整一致。
#[derive(Debug)]
pub struct DecisionBuilder<'a> {
    diag: &'a mut Diagnostics,
    seq: u64,
    request_id: String,
}

impl<'a> DecisionBuilder<'a> {
    pub fn new(diag: &'a mut Diagnostics, seq: u64, request_id: impl Into<String>) -> Self {
        Self {
            diag,
            seq,
            request_id: request_id.into(),
        }
    }

    #[allow(clippy::too_many_arguments)]
    pub fn access(
        self,
        access: Access,
        verdict: Verdict,
        reason: Option<(ReasonCode, String)>,
        source: Option<HitSource>,
        state: StateView,
    ) {
        let (reason_code, detail) = reason
            .map(|(c, d)| (Some(c), d))
            .unwrap_or_else(|| (None, default_detail(verdict, access, source)));
        self.diag.record(DecisionRecord {
            seq: self.seq,
            request_id: self.request_id,
            op: "access",
            verdict,
            reason_code,
            detail,
            page: Some(access.page.as_u64()),
            kind: Some(access.kind),
            source,
            state,
        });
    }

    pub fn event(self, op: &'static str, verdict: Verdict, detail: String, state: StateView) {
        self.diag.record(DecisionRecord {
            seq: self.seq,
            request_id: self.request_id,
            op,
            verdict,
            reason_code: None,
            detail,
            page: None,
            kind: None,
            source: None,
            state,
        });
    }
}

fn default_detail(verdict: Verdict, access: Access, source: Option<HitSource>) -> String {
    match verdict {
        Verdict::Accepted => match source {
            Some(HitSource::T1) => format!("accepted: real hit in T1 for {}", access.page),
            Some(HitSource::T2) => format!("accepted: real hit in T2 for {}", access.page),
            Some(HitSource::GhostB1) => format!(
                "accepted: ghost hit in B1 for {}; metadata only, page re-fetched from store",
                access.page
            ),
            Some(HitSource::GhostB2) => format!(
                "accepted: ghost hit in B2 for {}; metadata only, page re-fetched from store",
                access.page
            ),
            Some(HitSource::Miss) | None => {
                format!(
                    "accepted: miss for {}; page fetched from store",
                    access.page
                )
            }
        },
        Verdict::Rejected => format!("rejected: {}", access.page),
        Verdict::Undetermined => format!("undetermined: {}", access.page),
    }
}

/// 载荷脱敏：只暴露长度和前几个字节的十六进制，绝不打印完整内容。
pub fn redact_payload(data: &[u8]) -> String {
    const HEAD: usize = 4;
    let head: String = data
        .iter()
        .take(HEAD)
        .map(|b| format!("{b:02x}"))
        .collect::<Vec<_>>()
        .join(" ");
    let suffix = if data.len() > HEAD { " …" } else { "" };
    format!("<redacted {} bytes; head: {head}{suffix}>", data.len())
}

/// 生成默认请求标识（调用方也可用 HTTP 头 `X-Request-Id` 覆盖）。
pub fn new_request_id(seq: u64) -> String {
    format!("req-{seq:08}")
}

/// 用于诊断展示的页标识（不含页内容）。
pub fn page_label(page: PageId) -> String {
    page.to_string()
}

/// 把回写错误类别转成稳定字符串，供失败轨迹断言。
pub fn error_kind_label(kind: WriteBackError) -> &'static str {
    match kind {
        WriteBackError::Transient => "transient",
        WriteBackError::Permanent => "permanent",
    }
}
