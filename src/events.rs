//! 诊断事件环：为每次操作生成可重放的 `run_id`，保留关键中间状态与判断理由。
//!
//! - `run_id` 形如 `20261002T073000Z-000007`（UTC 时间戳 + 进程内单调序号）。
//! - 事件只追加，容量到上限后覆盖最旧事件（环形），但每个事件自身的
//!   run_id/输入/中间状态/判定理由完整保留，可据以重放问题。
//! - 每次翻译（含页故障）都记录一条结构化轨迹。

use serde::Serialize;
use std::collections::VecDeque;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

static GLOBAL_SEQ: AtomicU64 = AtomicU64::new(1);

/// 生成进程内唯一 run_id。时间戳取自 UTC 墙钟，序号单调递增。
pub fn new_run_id() -> String {
    let seq = GLOBAL_SEQ.fetch_add(1, Ordering::Relaxed);
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let (y, mo, d, h, mi, s) = civil_from_unix(secs);
    format!("{y:04}{mo:02}{d:02}T{h:02}{mi:02}{s:02}Z-{seq:06}")
}

// Howard Hinnant 的公历换算（输入为 Unix 秒，先除 86400 取天）。
fn civil_from_unix(secs: u64) -> (i64, u32, u32, u32, u32, u32) {
    let secs = secs as i64;
    let day = secs.div_euclid(86_400);
    let secs_of_day = secs.rem_euclid(86_400) as u32;

    let z = day + 719_468;
    let era = (if z >= 0 { z } else { z - 146_096 }) / 146_097;
    let doe = z - era * 146_097; // [0, 146096]
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let year = if m <= 2 { y + 1 } else { y };
    (
        year,
        m,
        d,
        secs_of_day / 3600,
        (secs_of_day % 3600) / 60,
        secs_of_day % 60,
    )
}

/// 操作类别（便于按类型过滤日志）。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EventKind {
    CreateAsid,
    Map,
    Unmap,
    Protect,
    TranslateOk,
    TranslateFault,
    Invalidate,
    Persist,
    Reset,
}

#[derive(Debug, Clone, Serialize)]
pub struct Event {
    pub run_id: String,
    pub seq: u64,
    pub kind: EventKind,
    /// 人类可读的一句话结论。
    pub summary: String,
    /// 判断理由（为什么是这个结论）。
    pub rationale: Vec<String>,
    /// 关键中间状态（走表轨迹、TLB 命中、拆分段等自由结构）。
    pub state: serde_json::Value,
    /// 失败类别（成功为 None）：fault_code / error_category+code。
    pub failure: Option<FailureTag>,
}

#[derive(Debug, Clone, Serialize)]
pub struct FailureTag {
    /// `page_fault` 或四类工程错误之一。
    pub class: String,
    pub code: String,
    pub detail: String,
}

/// 有界事件环。
#[derive(Debug)]
pub struct EventLog {
    capacity: usize,
    events: VecDeque<Event>,
    dropped: u64,
}

impl EventLog {
    pub fn new(capacity: usize) -> Self {
        Self {
            capacity: capacity.max(1),
            events: VecDeque::with_capacity(capacity.max(1)),
            dropped: 0,
        }
    }

    pub fn record(
        &mut self,
        kind: EventKind,
        summary: impl Into<String>,
        rationale: Vec<String>,
        state: serde_json::Value,
        failure: Option<FailureBuilder>,
    ) -> String {
        self.record_with_id(new_run_id(), kind, summary, rationale, state, failure)
    }

    /// 用调用方预先取得的 run_id 记录（保证响应负载与事件日志中的编号一致）。
    pub fn record_with_id(
        &mut self,
        run_id: String,
        kind: EventKind,
        summary: impl Into<String>,
        rationale: Vec<String>,
        state: serde_json::Value,
        failure: Option<FailureBuilder>,
    ) -> String {
        if self.events.len() >= self.capacity {
            self.events.pop_front();
            self.dropped += 1;
        }
        self.events.push_back(Event {
            run_id: run_id.clone(),
            seq: self.events.len() as u64 + self.dropped + 1,
            kind,
            summary: summary.into(),
            rationale,
            state,
            failure: failure.map(|f| f.0),
        });
        run_id
    }

    pub fn len(&self) -> usize {
        self.events.len()
    }
    pub fn is_empty(&self) -> bool {
        self.events.is_empty()
    }
    pub fn dropped(&self) -> u64 {
        self.dropped
    }
    pub fn capacity(&self) -> usize {
        self.capacity
    }

    pub fn recent(&self, n: usize) -> Vec<&Event> {
        self.events.iter().rev().take(n).collect()
    }

    pub fn find(&self, run_id: &str) -> Option<&Event> {
        self.events.iter().find(|e| e.run_id == run_id)
    }

    pub fn all(&self) -> Vec<&Event> {
        self.events.iter().collect()
    }

    pub fn snapshot_json(&self) -> serde_json::Value {
        serde_json::json!({
            "capacity": self.capacity,
            "stored": self.events.len(),
            "dropped_oldest": self.dropped,
            "events": self.events,
        })
    }

    pub fn clear(&mut self) {
        self.events.clear();
        self.dropped = 0;
    }
}

/// 让 record 调用点写起来短一点的辅助类型。
pub struct FailureBuilder(pub FailureTag);

impl FailureBuilder {
    pub fn fault(code: &str, detail: impl Into<String>) -> Self {
        Self(FailureTag {
            class: "page_fault".into(),
            code: code.into(),
            detail: detail.into(),
        })
    }
    pub fn error(class: &str, code: &str, detail: impl Into<String>) -> Self {
        Self(FailureTag {
            class: class.into(),
            code: code.into(),
            detail: detail.into(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn run_ids_are_unique_and_sortable() {
        let a = new_run_id();
        let b = new_run_id();
        assert_ne!(a, b);
        // 同秒内序号递增，字符串字典序与时间序一致。
        assert!(b > a, "a={a} b={b}");
    }

    #[test]
    fn civil_date_matches_known_unix_timestamps() {
        // 1970-01-01 00:00:00 UTC
        assert_eq!(civil_from_unix(0), (1970, 1, 1, 0, 0, 0));
        // 2026-10-02 07:30:15 UTC（固定夹具）
        let ts = 1_790_926_215;
        assert_eq!(civil_from_unix(ts), (2026, 10, 2, 7, 30, 15));
        // 2000-02-29 12:00:00 UTC（闰年）
        assert_eq!(civil_from_unix(951_825_600), (2000, 2, 29, 12, 0, 0));
    }

    #[test]
    fn ring_drops_oldest_and_keeps_latest() {
        let mut log = EventLog::new(2);
        log.record(EventKind::Reset, "r1", vec![], serde_json::json!({}), None);
        log.record(EventKind::Reset, "r2", vec![], serde_json::json!({}), None);
        log.record(EventKind::Reset, "r3", vec![], serde_json::json!({}), None);
        assert_eq!(log.len(), 2);
        assert_eq!(log.dropped(), 1);
        let recent = log.recent(10);
        assert_eq!(recent[0].summary, "r3");
        assert_eq!(recent[1].summary, "r2");
    }

    #[test]
    fn can_find_by_run_id_for_replay() {
        let mut log = EventLog::new(4);
        let id = log.record(
            EventKind::Map,
            "m",
            vec![],
            serde_json::json!({"x":1}),
            None,
        );
        let e = log.find(&id).expect("按 run_id 可检索");
        assert_eq!(e.state["x"], 1);
    }
}
