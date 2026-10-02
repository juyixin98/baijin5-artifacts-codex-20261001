//! Run-level cost accounting and the explainable run summary.

use crate::engine::EngineOutput;
use crate::model::DeviceModel;
use crate::request::{FinalStatus, RequestId};
use serde::{Deserialize, Serialize};

/// Per-request result. Every original request id appears exactly once, even
/// if it was merged into a larger item for dispatch.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RequestOutcome {
    pub id: RequestId,
    pub status: FinalStatus,
    pub arrival_ns: u64,
    /// None if never dispatched (cancelled while queued).
    pub dispatch_ns: Option<u64>,
    /// Completion time for dispatched requests; cancel time otherwise.
    pub finish_ns: u64,
    /// dispatch - arrival; None if never dispatched.
    pub wait_ns: Option<u64>,
    /// Device time attributed to this request. For merged items each member
    /// reports the shared item's service time (documented, not divided).
    pub service_ns: u64,
    /// Other original requests dispatched in the same merged item.
    pub merged_with: Vec<RequestId>,
    pub deadline_ns: Option<u64>,
    pub deadline_miss: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RunSummary {
    pub run_id: String,
    pub trace_name: String,
    pub scheduler: String,
    pub device: DeviceModel,
    /// Why these numbers must not be read as hardware measurements.
    pub device_caveat: String,
    /// Device busy span: timestamp of the last dispatched-item completion.
    pub makespan_ns: u64,
    /// Sum of service times of dispatched items (device busy time).
    pub total_service_ns: u64,
    pub total_seek_ns: u64,
    pub total_seek_distance_sectors: u64,
    pub head_final_sector: u64,
    pub arrived: usize,
    pub completed: usize,
    pub cancelled_before_dispatch: usize,
    pub cancelled_after_dispatch: usize,
    pub cancel_rejected: u64,
    /// Ids of completed requests whose finish exceeded arrival + deadline.
    pub deadline_misses: Vec<RequestId>,
    pub outcomes: Vec<RequestOutcome>,
    /// Conclusions that are explicitly uncertain, listed separately.
    pub uncertain: Vec<String>,
}

pub fn summarize(
    run_id: String,
    trace_name: &str,
    scheduler_name: &str,
    device: &DeviceModel,
    out: EngineOutput,
) -> (RunSummary, Vec<crate::engine::Event>) {
    let mut completed = 0usize;
    let mut cancelled_before = 0usize;
    let mut cancelled_after = 0usize;
    let mut deadline_misses = Vec::new();
    let mut uncertain = vec![device.caveat().to_string()];
    for o in &out.outcomes {
        match o.status {
            FinalStatus::Completed => completed += 1,
            FinalStatus::CancelledBeforeDispatch => cancelled_before += 1,
            FinalStatus::CancelledAfterDispatch => {
                cancelled_after += 1;
                uncertain.push(format!(
                    "request {} was cancelled after dispatch: the device operation ran to completion, so the state of its data is unknown",
                    o.id
                ));
            }
        }
        if o.deadline_miss {
            deadline_misses.push(o.id.clone());
        }
    }
    let summary = RunSummary {
        run_id,
        trace_name: trace_name.to_string(),
        scheduler: scheduler_name.to_string(),
        device: device.clone(),
        device_caveat: device.caveat().to_string(),
        makespan_ns: out.makespan_ns,
        total_service_ns: out.total_service_ns,
        total_seek_ns: out.total_seek_ns,
        total_seek_distance_sectors: out.total_seek_distance_sectors,
        head_final_sector: out.head_final_sector,
        arrived: completed + cancelled_before + cancelled_after,
        completed,
        cancelled_before_dispatch: cancelled_before,
        cancelled_after_dispatch: cancelled_after,
        cancel_rejected: out.cancel_rejected,
        deadline_misses,
        outcomes: out.outcomes,
        uncertain,
    };
    (summary, out.events)
}
