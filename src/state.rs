//! 运行结果的文件系统持久化：每次运行一个目录，事件日志 JSONL + 汇总 JSON。
//!
//! 布局：
//!   <data_dir>/runs/<run_id>/report.json   —— 完整报告（含指标与完成记录）
//!   <data_dir>/runs/<run_id>/events.jsonl  —— 逐条事件日志（可解释审计）
//!   <data_dir>/index.json                  —— 运行索引（采样状态：只存摘要）

use crate::api::CompareReport;
use crate::model::{ErrorCategory, ModelError};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

/// 索引中的一条运行摘要（采样状态，不含事件明细）。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunSummary {
    pub run_id: String,
    pub created_at_ms: u64,
    pub schedulers: Vec<String>,
    pub submitted: usize,
}

#[derive(Debug, Default, Clone, Serialize, Deserialize)]
struct Index {
    next_seq: u64,
    runs: Vec<RunSummary>,
}

pub struct RunStore {
    root: PathBuf,
}

impl RunStore {
    pub fn new(root: impl Into<PathBuf>) -> std::io::Result<Self> {
        let root = root.into();
        std::fs::create_dir_all(root.join("runs"))?;
        let store = RunStore { root };
        if !store.index_path().exists() {
            store.save_index(&Index::default())?;
        }
        Ok(store)
    }

    fn index_path(&self) -> PathBuf {
        self.root.join("index.json")
    }

    fn run_dir(&self, run_id: &str) -> PathBuf {
        self.root.join("runs").join(run_id)
    }

    fn load_index(&self) -> Result<Index, ModelError> {
        let text = std::fs::read_to_string(self.index_path()).map_err(|e| {
            ModelError::new(
                ErrorCategory::RunNotFound,
                format!("cannot read run index: {e}"),
            )
        })?;
        serde_json::from_str(&text).map_err(|e| {
            ModelError::new(
                ErrorCategory::RunNotFound,
                format!("corrupt run index: {e}"),
            )
        })
    }

    fn save_index(&self, index: &Index) -> std::io::Result<()> {
        let text = serde_json::to_string_pretty(index).expect("index serialization is infallible");
        std::fs::write(self.index_path(), text)
    }

    /// 持久化一份报告，返回分配的 run_id（单调递增，可复现）。
    pub fn save_report(&self, report: &CompareReport) -> Result<String, ModelError> {
        let mut index = self.load_index()?;
        index.next_seq += 1;
        let run_id = format!("run-{:06}", index.next_seq);
        let dir = self.run_dir(&run_id);
        std::fs::create_dir_all(&dir).map_err(|e| {
            ModelError::new(
                ErrorCategory::RunNotFound,
                format!("cannot create run dir: {e}"),
            )
        })?;

        let mut stored = report.clone();
        stored.run_id = run_id.clone();
        let report_text = serde_json::to_string_pretty(&stored)
            .map_err(|e| ModelError::new(ErrorCategory::RunNotFound, format!("serialize: {e}")))?;
        std::fs::write(dir.join("report.json"), report_text).map_err(|e| {
            ModelError::new(ErrorCategory::RunNotFound, format!("write report: {e}"))
        })?;

        // 事件日志单独成 JSONL，一行一事，便于 grep / diff。
        let mut lines = String::new();
        for outcome in &stored.results {
            for event in &outcome.events {
                let line = serde_json::json!({
                    "scheduler": outcome.scheduler,
                    "event": event,
                });
                lines.push_str(&line.to_string());
                lines.push('\n');
            }
        }
        std::fs::write(dir.join("events.jsonl"), lines).map_err(|e| {
            ModelError::new(ErrorCategory::RunNotFound, format!("write events: {e}"))
        })?;

        index.runs.push(RunSummary {
            run_id: run_id.clone(),
            created_at_ms: stored.created_at_ms,
            schedulers: stored.results.iter().map(|r| r.scheduler.clone()).collect(),
            submitted: stored
                .results
                .first()
                .map(|r| r.metrics.submitted)
                .unwrap_or(0),
        });
        self.save_index(&index).map_err(|e| {
            ModelError::new(ErrorCategory::RunNotFound, format!("write index: {e}"))
        })?;
        Ok(run_id)
    }

    pub fn list_runs(&self) -> Result<Vec<RunSummary>, ModelError> {
        Ok(self.load_index()?.runs)
    }

    pub fn load_report(&self, run_id: &str) -> Result<CompareReport, ModelError> {
        let path = self.run_dir(run_id).join("report.json");
        let text = std::fs::read_to_string(&path).map_err(|_| {
            ModelError::new(
                ErrorCategory::RunNotFound,
                format!("no stored report for run id '{run_id}'"),
            )
        })?;
        serde_json::from_str(&text).map_err(|e| {
            ModelError::new(
                ErrorCategory::RunNotFound,
                format!("corrupt report {run_id}: {e}"),
            )
        })
    }

    pub fn events_path(&self, run_id: &str) -> Result<PathBuf, ModelError> {
        let path = self.run_dir(run_id).join("events.jsonl");
        if Path::new(&path).exists() {
            Ok(path)
        } else {
            Err(ModelError::new(
                ErrorCategory::RunNotFound,
                format!("no stored events for run id '{run_id}'"),
            ))
        }
    }
}
