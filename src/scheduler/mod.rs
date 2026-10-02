//! 调度器抽象：把“排队中的请求”与“一次下发决定”分开。
//!
//! 一次下发（`Dispatch`）可能是一个**合并组**：若干同方向、扇区相邻的原始请求
//! 合成一次设备服务，但 `requests` 完整保留每个原始请求的身份，完成时逐个记账。

use crate::model::{Direction, RequestSpec};

pub mod deadline;
pub mod scan;

/// 已进入调度器队列、尚未下发的请求。
#[derive(Debug, Clone)]
pub struct QueuedRequest {
    pub id: String,
    pub lba: u64,
    pub sectors: u64,
    pub direction: Direction,
    pub arrival_ms: u64,
    /// 绝对截止时刻（arrival + 相对 deadline），仅 deadline 调度器消费。
    pub deadline_ms: u64,
}

impl QueuedRequest {
    pub fn from_spec(spec: &RequestSpec, default_deadline_ms: u64) -> Self {
        let relative = spec.deadline_ms.unwrap_or(default_deadline_ms);
        QueuedRequest {
            id: spec.id.clone(),
            lba: spec.lba,
            sectors: spec.sectors,
            direction: spec.direction,
            arrival_ms: spec.arrival_ms,
            deadline_ms: spec.arrival_ms.saturating_add(relative),
        }
    }

    pub fn end_lba(&self) -> u64 {
        self.lba + self.sectors
    }
}

/// 一次下发决定：合并组 + 人类可解释的选择原因。
#[derive(Debug, Clone)]
pub struct Dispatch {
    /// 组内原始请求（>=1），按扇区顺序排列；身份全部保留。
    pub requests: Vec<QueuedRequest>,
    /// 选择原因，例如 `deadline_expired:read_head expired at t=600 (now=700)`。
    /// 该字符串会进入事件日志，是“防饥饿规则可解释”的载体。
    pub reason: String,
}

impl Dispatch {
    /// 合并组的总扇区数（组内保证相邻，故等于连续跨度）。
    pub fn total_sectors(&self) -> u64 {
        self.requests.iter().map(|r| r.sectors).sum()
    }

    pub fn start_lba(&self) -> u64 {
        self.requests.first().map(|r| r.lba).unwrap_or(0)
    }

    pub fn ids(&self) -> Vec<String> {
        self.requests.iter().map(|r| r.id.clone()).collect()
    }
}

/// 调度器接口。引擎在每个可下发时刻调用 `pick_next`。
pub trait Scheduler {
    fn name(&self) -> &'static str;
    /// 新请求到达并入队。
    fn enqueue(&mut self, req: QueuedRequest);
    /// 取消一个仍在队列中的请求；返回是否命中（已下发的不在队列里）。
    fn remove_queued(&mut self, request_id: &str) -> bool;
    /// 在 `now_ms`（虚拟时钟，f64 毫秒）、磁头位于 `head_lba` 时选择下一个下发组。
    fn pick_next(&mut self, now_ms: f64, head_lba: u64) -> Option<Dispatch>;
    /// 当前队列长度（诊断用）。
    fn queued_len(&self) -> usize;
}

/// 从 `queue` 的 `start` 位置出发，向后吸收同方向且扇区相邻的请求，形成合并组。
/// 返回组内最后一个元素的下标。调用方负责把 `[start..=end]` 从队列中移除。
pub(crate) fn extend_merge(queue: &[QueuedRequest], start: usize) -> usize {
    let mut end = start;
    while end + 1 < queue.len() {
        let cur = &queue[end];
        let next = &queue[end + 1];
        if next.direction == cur.direction && next.lba == cur.end_lba() {
            end += 1;
        } else {
            break;
        }
    }
    end
}

/// 与 `extend_merge` 对称：从 `start` 向低 LBA 方向吸收相邻同方向请求，
/// 返回组内最前一个元素的下标（用于 SCAN 下行扫描）。
pub(crate) fn extend_merge_backward(queue: &[QueuedRequest], start: usize) -> usize {
    let mut begin = start;
    while begin > 0 {
        let prev = &queue[begin - 1];
        let cur = &queue[begin];
        if prev.direction == cur.direction && prev.end_lba() == cur.lba {
            begin -= 1;
        } else {
            break;
        }
    }
    begin
}
