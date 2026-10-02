//! 运行日志：环形内存缓冲 + 可选 JSONL 落盘。
//!
//! 每次翻译/访存都分配一个单调递增的**运行编号**（run_id），记录：
//! 输入摘要、关键中间状态（walk steps、TLB 来源）、判断理由与结果类别。
//! run_id 与 JSONL 行可用于离线重放问题。

use crate::config::RUN_LOG_CAP;
use serde_json::Value;
use std::collections::VecDeque;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::Path;
use std::sync::{Arc, Mutex};

#[derive(Debug, Clone, serde::Serialize)]
pub struct RunEvent {
    pub run_id: u64,
    /// 同一进程内的顺序号（从 1 开始）。
    pub seq: u64,
    pub op: String,
    pub asid: Option<u16>,
    /// 结果类别：ok / page_fault / input_error / conflict / resource_exhausted / compute_failure
    pub outcome: String,
    pub summary: String,
    pub detail: Value,
    pub justification: String,
}

#[derive(Debug, Clone, Default)]
pub struct RunLog {
    inner: Arc<Mutex<RunLogInner>>,
}

#[derive(Debug)]
struct RunLogInner {
    next_id: u64,
    events: VecDeque<RunEvent>,
    sink_path: Option<std::path::PathBuf>,
    dropped: u64,
}

impl Default for RunLogInner {
    fn default() -> Self {
        RunLogInner {
            next_id: 1,
            events: VecDeque::new(),
            sink_path: None,
            dropped: 0,
        }
    }
}

impl RunLog {
    pub fn new() -> Self {
        RunLog::default()
    }

    /// 绑定 JSONL 追加落盘文件（诊断采样状态持久化）。
    pub fn with_jsonl_sink(path: &Path) -> std::io::Result<Self> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let _ = OpenOptions::new().create(true).append(true).open(path)?;
        let inner = RunLogInner {
            sink_path: Some(path.to_path_buf()),
            ..RunLogInner::default()
        };
        Ok(RunLog {
            inner: Arc::new(Mutex::new(inner)),
        })
    }

    /// 记录一条事件，返回其 run_id。
    pub fn record(
        &self,
        op: impl Into<String>,
        asid: Option<u16>,
        outcome: impl Into<String>,
        summary: impl Into<String>,
        detail: Value,
        justification: impl Into<String>,
    ) -> u64 {
        let mut g = self.inner.lock().unwrap();
        let run_id = g.next_id;
        g.next_id += 1;
        let seq = run_id;
        let event = RunEvent {
            run_id,
            seq,
            op: op.into(),
            asid,
            outcome: outcome.into(),
            summary: summary.into(),
            detail,
            justification: justification.into(),
        };
        if let Some(path) = &g.sink_path {
            if let Ok(line) = serde_json::to_string(&event) {
                if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(path) {
                    let _ = writeln!(f, "{line}");
                }
            }
        }
        if g.events.len() >= RUN_LOG_CAP {
            g.events.pop_front();
            g.dropped += 1;
        }
        g.events.push_back(event);
        run_id
    }

    pub fn get(&self, run_id: u64) -> Option<RunEvent> {
        let g = self.inner.lock().unwrap();
        g.events.iter().find(|e| e.run_id == run_id).cloned()
    }

    /// 取最近 `limit` 条（按时间正序）。
    pub fn recent(&self, limit: usize) -> Vec<RunEvent> {
        let g = self.inner.lock().unwrap();
        let available = g.events.len();
        let take = limit.min(available);
        g.events.iter().skip(available - take).cloned().collect()
    }

    pub fn counters(&self) -> LogCounters {
        let g = self.inner.lock().unwrap();
        LogCounters {
            next_run_id: g.next_id,
            retained: g.events.len(),
            capacity: RUN_LOG_CAP,
            dropped_from_memory: g.dropped,
            sink: g.sink_path.as_ref().map(|p| p.display().to_string()),
        }
    }
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct LogCounters {
    pub next_run_id: u64,
    pub retained: usize,
    pub capacity: usize,
    pub dropped_from_memory: u64,
    pub sink: Option<String>,
}
