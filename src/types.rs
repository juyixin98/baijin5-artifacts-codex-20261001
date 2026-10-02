//! 共享数据模型：页标识、访问类型、访问结果、统计快照。
//!
//! 这些类型被算法、引擎、存储适配器、轨迹回放和 HTTP 层共同使用，
//! 因此单独成模块，避免任何一方反向依赖。

use serde::{Deserialize, Serialize};

/// 页标识。合成回放中为非负整数；`newtype` 防止把“页号”和普通整数混用。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct PageId(pub u64);

impl PageId {
    pub fn as_u64(self) -> u64 {
        self.0
    }
}

impl std::fmt::Display for PageId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "page#{}", self.0)
    }
}

/// 访问类型：读不产生脏页；写在缓存命中/装入时把页标记为脏。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum AccessKind {
    Read,
    Write,
}

impl AccessKind {
    pub fn is_write(self) -> bool {
        matches!(self, AccessKind::Write)
    }
}

/// 一次访问请求（轨迹中的一行 / HTTP 请求的语义内核）。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Access {
    pub page: PageId,
    pub kind: AccessKind,
}

impl Access {
    pub const fn read(page: u64) -> Self {
        Self {
            page: PageId(page),
            kind: AccessKind::Read,
        }
    }
    pub const fn write(page: u64) -> Self {
        Self {
            page: PageId(page),
            kind: AccessKind::Write,
        }
    }
}

/// 命中来源。
///
/// ARC 的关键不变量：**幽灵命中（B1/B2）不等于数据已在缓存**。
/// 幽灵目录只记录元数据（页标识），命中后页必须重新从后端装入，
/// 因此 [`HitSource::GhostB1`] / [`HitSource::GhostB2`] 与
/// [`HitSource::T1`] / [`HitSource::T2`] 是严格区分的两类结果。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HitSource {
    /// 真实列表 T1（数据在缓存）。
    T1,
    /// 真实列表 T2（数据在缓存）。
    T2,
    /// 幽灵目录 B1（仅元数据，数据不在缓存，需重新装入）。
    GhostB1,
    /// 幽灵目录 B2（仅元数据，数据不在缓存，需重新装入）。
    GhostB2,
    /// 所有列表均未命中（数据不在缓存，需装入）。
    Miss,
}

impl HitSource {
    /// 数据此刻是否真的在缓存中。只有 T1/T2 为真。
    pub fn is_resident(self) -> bool {
        matches!(self, HitSource::T1 | HitSource::T2)
    }

    pub fn is_ghost(self) -> bool {
        matches!(self, HitSource::GhostB1 | HitSource::GhostB2)
    }
}

/// 单次访问的处理结果。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct AccessOutcome {
    pub page: PageId,
    pub kind: AccessKind,
    /// 算法判定的命中来源（处理前所在位置）。
    pub source: HitSource,
    /// 本次访问后页是否驻留在真实缓存中。
    /// 幽灵命中与普通缺页在此刻才变为 `true`（且必须经过后端装入）。
    pub resident_after: bool,
    /// 本次访问是否触发了一次淘汰。
    pub evicted: bool,
    /// 被淘汰页（若有）。脏页的回写由引擎负责，算法层不感知存储。
    pub evicted_page: Option<PageId>,
    /// 本次访问后自适应目标值 p。
    pub p_after: usize,
}

/// 一次回写动作的结果类别。独立测试按具体类别断言失败轨迹。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WriteBackError {
    /// 适配器临时失败（如注入的瞬时 I/O 错误），调用方可重试。
    Transient,
    /// 适配器永久失败（如路径非法），不应继续重试同一页。
    Permanent,
}

impl std::fmt::Display for WriteBackError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            WriteBackError::Transient => write!(f, "transient write-back failure"),
            WriteBackError::Permanent => write!(f, "permanent write-back failure"),
        }
    }
}

impl std::error::Error for WriteBackError {}

/// 缓存结构与统计快照（诊断接口 / 逐步对照测试使用）。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CacheStats {
    pub c: usize,
    pub p: usize,
    pub t1_len: usize,
    pub t2_len: usize,
    pub b1_len: usize,
    pub b2_len: usize,
    /// 真实缓存中的页数（恒等于 `t1_len + t2_len`，且不超过 c）。
    pub resident: usize,
    pub hits_t1: u64,
    pub hits_t2: u64,
    pub ghost_hits_b1: u64,
    pub ghost_hits_b2: u64,
    pub misses: u64,
    /// 真实命中总次数（T1+T2）。
    pub hits: u64,
    /// 因容量为零而被直接拒绝的访问数。
    pub rejected_zero_capacity: u64,
    /// 因回写失败而无法完成的访问数。
    pub writeback_failures: u64,
    /// 累计成功回写的脏页数。
    pub writebacks: u64,
    /// 累计装入（fetch）次数——幽灵命中与缺页都会产生装入。
    pub fetches: u64,
}

impl CacheStats {
    pub fn hit_rate(&self) -> f64 {
        let total = self.hits + self.ghost_hits_b1 + self.ghost_hits_b2 + self.misses;
        if total == 0 {
            0.0
        } else {
            self.hits as f64 / total as f64
        }
    }
}
