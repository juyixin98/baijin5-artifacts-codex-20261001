//! 仿真引擎：离散事件循环，驱动“到达 → 排队 → 下发 → 完成 / 取消”。
//!
//! 时间模型：虚拟时钟（毫秒，f64），只随事件前进，完全可控、可复现。
//! 同一时刻的事件处理顺序固定为：完成 → 到达 → 取消 → 下发决策，
//! 保证结果确定性。

use crate::model::{DeviceModel, Direction, ModelError, Trace, validate_trace};
use crate::scheduler::{Dispatch, QueuedRequest, Scheduler};
use serde::{Deserialize, Serialize};

/// 取消结果类别——“已下发”与“未下发”语义明确不同。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CancelOutcome {
    /// 请求还在队列中，成功移除，不会再完成。
    CancelledQueued,
    /// 请求已下发到设备，无法撤回；它会正常完成。
    RejectedInFlight,
    /// 请求已处于终态（已完成或已取消）。
    RejectedTerminal,
    /// 请求尚未到达（取消时刻早于其 arrival_ms）。
    RejectedNotArrived,
}

/// 事件日志条目：每个关键步骤一条，全部携带请求身份与原因。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Event {
    Arrived {
        at_ms: f64,
        request_id: String,
        lba: u64,
        sectors: u64,
        direction: Direction,
    },
    Dispatched {
        at_ms: f64,
        /// 合并组内全部原始请求 id（身份不丢）。
        request_ids: Vec<String>,
        start_lba: u64,
        total_sectors: u64,
        service_ms: f64,
        /// 调度器给出的可解释选择原因。
        reason: String,
    },
    Completed {
        at_ms: f64,
        request_id: String,
        wait_ms: f64,
        turnaround_ms: f64,
        deadline_ms: u64,
        deadline_missed: bool,
        /// 同组完成的其他请求 id（合并证据）。
        merged_with: Vec<String>,
    },
    Cancelled {
        at_ms: f64,
        request_id: String,
        outcome: CancelOutcome,
        detail: String,
    },
}

/// 单请求完成记录（指标计算的原料，也直接暴露给诊断接口）。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CompletionRecord {
    pub request_id: String,
    pub direction: Direction,
    pub lba: u64,
    pub sectors: u64,
    pub arrival_ms: u64,
    pub dispatch_ms: f64,
    pub finish_ms: f64,
    pub wait_ms: f64,
    pub turnaround_ms: f64,
    pub deadline_ms: u64,
    pub deadline_missed: bool,
    pub merged_with: Vec<String>,
}

/// 一次运行的汇总指标。
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Metrics {
    pub submitted: usize,
    pub completed: usize,
    pub cancelled_queued: usize,
    pub cancel_rejected_in_flight: usize,
    pub cancel_rejected_terminal: usize,
    pub cancel_rejected_not_arrived: usize,
    pub deadline_misses: usize,
    pub total_wait_ms: f64,
    pub max_wait_ms: f64,
    pub total_turnaround_ms: f64,
    /// 磁头总移动距离（扇区），成本核算用。
    pub total_seek_sectors: u64,
    pub total_service_ms: f64,
    pub makespan_ms: f64,
    /// 实际下发顺序（合并组展开为原始 id 序列）。
    pub dispatch_order: Vec<String>,
}

/// 单个调度器跑完一条轨迹的完整结果。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunOutcome {
    pub scheduler: String,
    pub events: Vec<Event>,
    pub completions: Vec<CompletionRecord>,
    pub metrics: Metrics,
}

/// 在飞请求（已下发未完成）。
struct InFlight {
    dispatch: Dispatch,
    dispatch_ms: f64,
    finish_ms: f64,
}

/// 引擎：给定设备模型，用指定调度器跑一条轨迹。
pub struct Engine {
    device: DeviceModel,
}

impl Engine {
    pub fn new(device: DeviceModel) -> Result<Self, ModelError> {
        device.validate()?;
        Ok(Engine { device })
    }

    pub fn device(&self) -> &DeviceModel {
        &self.device
    }

    /// 运行仿真。输入校验失败时返回全部错误，不产生任何事件。
    pub fn run(
        &self,
        trace: &Trace,
        scheduler: &mut dyn Scheduler,
        default_read_expire_ms: u64,
        default_write_expire_ms: u64,
    ) -> Result<RunOutcome, Vec<ModelError>> {
        let errors = validate_trace(trace, &self.device);
        if !errors.is_empty() {
            return Err(errors);
        }

        let mut arrivals: Vec<&crate::model::RequestSpec> = trace.requests.iter().collect();
        arrivals.sort_by(|a, b| (a.arrival_ms, &a.id).cmp(&(b.arrival_ms, &b.id)));
        let mut cancels: Vec<&crate::model::CancelSpec> = trace.cancels.iter().collect();
        cancels.sort_by_key(|c| c.at_ms);

        let mut events = Vec::new();
        let mut completions: Vec<CompletionRecord> = Vec::new();
        let mut metrics = Metrics {
            submitted: trace.requests.len(),
            ..Default::default()
        };

        let mut now: f64 = 0.0;
        let mut head_lba: u64 = 0;
        let mut last_finish_ms: f64 = 0.0;
        let mut in_flight: Option<InFlight> = None;
        let mut arrival_idx = 0;
        let mut cancel_idx = 0;
        // 已终态的请求 id（完成或已取消），用于区分取消类别。
        let mut terminal: std::collections::HashSet<String> = std::collections::HashSet::new();

        loop {
            // 1. 完成事件：在飞请求到点完成。
            if in_flight.as_ref().is_some_and(|f| f.finish_ms <= now) {
                let flight = in_flight.take().unwrap();
                last_finish_ms = now;
                head_lba = flight.dispatch.start_lba() + flight.dispatch.total_sectors();
                let group_ids = flight.dispatch.ids();
                for req in &flight.dispatch.requests {
                    let wait = flight.dispatch_ms - req.arrival_ms as f64;
                    let turnaround = now - req.arrival_ms as f64;
                    let missed = now > req.deadline_ms as f64;
                    if missed {
                        metrics.deadline_misses += 1;
                    }
                    metrics.total_wait_ms += wait;
                    metrics.max_wait_ms = metrics.max_wait_ms.max(wait);
                    metrics.total_turnaround_ms += turnaround;
                    let merged_with: Vec<String> = group_ids
                        .iter()
                        .filter(|id| *id != &req.id)
                        .cloned()
                        .collect();
                    events.push(Event::Completed {
                        at_ms: now,
                        request_id: req.id.clone(),
                        wait_ms: wait,
                        turnaround_ms: turnaround,
                        deadline_ms: req.deadline_ms,
                        deadline_missed: missed,
                        merged_with: merged_with.clone(),
                    });
                    completions.push(CompletionRecord {
                        request_id: req.id.clone(),
                        direction: req.direction,
                        lba: req.lba,
                        sectors: req.sectors,
                        arrival_ms: req.arrival_ms,
                        dispatch_ms: flight.dispatch_ms,
                        finish_ms: now,
                        wait_ms: wait,
                        turnaround_ms: turnaround,
                        deadline_ms: req.deadline_ms,
                        deadline_missed: missed,
                        merged_with,
                    });
                    terminal.insert(req.id.clone());
                }
                metrics.completed += flight.dispatch.requests.len();
            }

            // 2. 到达事件。
            while arrival_idx < arrivals.len() && arrivals[arrival_idx].arrival_ms as f64 <= now {
                let spec = arrivals[arrival_idx];
                arrival_idx += 1;
                let default_expire = match spec.direction {
                    Direction::Read => default_read_expire_ms,
                    Direction::Write => default_write_expire_ms,
                };
                let queued = QueuedRequest::from_spec(spec, default_expire);
                events.push(Event::Arrived {
                    at_ms: now,
                    request_id: queued.id.clone(),
                    lba: queued.lba,
                    sectors: queued.sectors,
                    direction: queued.direction,
                });
                scheduler.enqueue(queued);
            }

            // 3. 取消事件：未下发可撤，已下发拒绝。
            while cancel_idx < cancels.len() && cancels[cancel_idx].at_ms as f64 <= now {
                let cancel = cancels[cancel_idx];
                cancel_idx += 1;
                let (outcome, detail) = if terminal.contains(&cancel.request_id) {
                    metrics.cancel_rejected_terminal += 1;
                    (
                        CancelOutcome::RejectedTerminal,
                        "request already completed or cancelled".to_string(),
                    )
                } else if in_flight
                    .as_ref()
                    .is_some_and(|f| f.dispatch.ids().contains(&cancel.request_id))
                {
                    metrics.cancel_rejected_in_flight += 1;
                    (
                        CancelOutcome::RejectedInFlight,
                        "request already dispatched to device; it will complete normally"
                            .to_string(),
                    )
                } else if scheduler.remove_queued(&cancel.request_id) {
                    metrics.cancelled_queued += 1;
                    terminal.insert(cancel.request_id.clone());
                    (
                        CancelOutcome::CancelledQueued,
                        "removed from scheduler queue".to_string(),
                    )
                } else if arrivals[arrival_idx..]
                    .iter()
                    .any(|s| s.id == cancel.request_id)
                {
                    // 请求在轨迹里存在但还没到到达时刻。
                    metrics.cancel_rejected_not_arrived += 1;
                    (
                        CancelOutcome::RejectedNotArrived,
                        "request has not arrived yet at cancel time".to_string(),
                    )
                } else {
                    // 校验已保证 id 存在且未终态；不在队列也不在本批在飞，只可能已被
                    // 并入早先的下发组——防御性分支，正常不可达。
                    metrics.cancel_rejected_in_flight += 1;
                    (
                        CancelOutcome::RejectedInFlight,
                        "request not found in queue; treated as in-flight".to_string(),
                    )
                };
                events.push(Event::Cancelled {
                    at_ms: now,
                    request_id: cancel.request_id.clone(),
                    outcome,
                    detail,
                });
            }

            // 4. 下发决策：设备空闲且调度器能给出批次。
            if in_flight.is_none()
                && let Some(dispatch) = scheduler.pick_next(now, head_lba)
            {
                let service = self.device.service_time_ms(
                    head_lba,
                    dispatch.start_lba(),
                    dispatch.total_sectors(),
                );
                metrics.total_seek_sectors += head_lba.abs_diff(dispatch.start_lba());
                metrics.total_service_ms += service;
                metrics.dispatch_order.extend(dispatch.ids());
                events.push(Event::Dispatched {
                    at_ms: now,
                    request_ids: dispatch.ids(),
                    start_lba: dispatch.start_lba(),
                    total_sectors: dispatch.total_sectors(),
                    service_ms: service,
                    reason: dispatch.reason.clone(),
                });
                in_flight = Some(InFlight {
                    dispatch,
                    dispatch_ms: now,
                    finish_ms: now + service,
                });
                continue; // 立即重新评估：完成时刻可能就在当前。
            }

            // 5. 推进时钟到下一个事件时刻。
            let mut next: Option<f64> = None;
            let mut consider = |t: f64| {
                if t > now && next.is_none_or(|n| t < n) {
                    next = Some(t);
                }
            };
            if let Some(flight) = &in_flight {
                consider(flight.finish_ms);
            }
            if arrival_idx < arrivals.len() {
                consider(arrivals[arrival_idx].arrival_ms as f64);
            }
            if cancel_idx < cancels.len() {
                consider(cancels[cancel_idx].at_ms as f64);
            }
            match next {
                Some(t) => now = t,
                None => break, // 无未来事件：队列与设备均已排空。
            }
        }

        // makespan = 最后一个完成时刻（不含后续空转的取消事件）。
        metrics.makespan_ms = last_finish_ms;
        Ok(RunOutcome {
            scheduler: scheduler.name().to_string(),
            events,
            completions,
            metrics,
        })
    }
}
