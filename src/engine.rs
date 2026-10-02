//! Discrete-event simulation engine: one device, one scheduler, virtual clock.
//!
//! Tie-break rules at equal timestamps (deterministic, documented):
//! 1. device completions, 2. arrivals, 3. cancels, 4. dispatch decision.
//! Consequences:
//! - a cancel at the exact tick a request would be dispatched still wins
//!   (the request is pending when the cancel is processed);
//! - a cancel at the exact tick a request completes sees it already finished
//!   and is rejected as `already_finished`.

use crate::cost::RequestOutcome;
use crate::model::DeviceModel;
use crate::request::{FinalStatus, RequestId};
use crate::sched::{Dispatch, Scheduler};
use crate::trace::{Trace, TraceOp};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Event {
    pub t_ns: u64,
    #[serde(flatten)]
    pub kind: EventKind,
    /// Request identities this event concerns (all members for merged items).
    pub request_ids: Vec<RequestId>,
    /// Human-readable explanation (merge notes, reject reasons, ...).
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub detail: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum EventKind {
    Arrived,
    Merged,
    Dispatched {
        reason: crate::sched::DispatchReason,
        head_sector: u64,
        service_ns: u64,
        finish_ns: u64,
    },
    Completed {
        service_ns: u64,
    },
    CancelledBeforeDispatch,
    /// Cancel arrived while the request was in-flight; service continues.
    CancelRequested,
    CancelledAfterDispatch {
        service_ns: u64,
    },
    CancelRejected {
        reason: CancelRejectReason,
    },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CancelRejectReason {
    UnknownId,
    AlreadyFinished,
}

struct InFlight {
    dispatch: Dispatch,
    dispatch_ns: u64,
    finish_ns: u64,
    service_ns: u64,
    cancel_requested: bool,
}

pub struct Engine {
    device: DeviceModel,
    scheduler: Box<dyn Scheduler>,
    head: u64,
    now: u64,
    inflight: Option<InFlight>,
    events: Vec<Event>,
    outcomes: Vec<RequestOutcome>,
    total_service_ns: u64,
    total_seek_ns: u64,
    total_seek_distance_sectors: u64,
    cancel_rejected: u64,
    last_completion_ns: u64,
}

#[derive(Debug)]
pub struct EngineOutput {
    pub events: Vec<Event>,
    pub outcomes: Vec<RequestOutcome>,
    pub total_service_ns: u64,
    pub total_seek_ns: u64,
    pub total_seek_distance_sectors: u64,
    pub cancel_rejected: u64,
    /// Device busy span: time of the last dispatched-item completion.
    pub makespan_ns: u64,
    pub head_final_sector: u64,
}

impl Engine {
    pub fn new(device: DeviceModel, scheduler: Box<dyn Scheduler>, head_start: u64) -> Self {
        Engine {
            device,
            scheduler,
            head: head_start,
            now: 0,
            inflight: None,
            events: Vec::new(),
            outcomes: Vec::new(),
            total_service_ns: 0,
            total_seek_ns: 0,
            total_seek_distance_sectors: 0,
            cancel_rejected: 0,
            last_completion_ns: 0,
        }
    }

    fn push(&mut self, kind: EventKind, ids: Vec<RequestId>, detail: String) {
        self.events.push(Event {
            t_ns: self.now,
            kind,
            request_ids: ids,
            detail,
        });
    }

    pub fn run(mut self, trace: &Trace) -> EngineOutput {
        let ops = &trace.ops;
        let mut idx = 0usize;
        loop {
            let next_op_t = ops.get(idx).map(|o| o.at_ns());
            let finish_t = self.inflight.as_ref().map(|f| f.finish_ns);
            let t = match (next_op_t, finish_t) {
                (Some(a), Some(f)) => a.min(f),
                (Some(a), None) => a,
                (None, Some(f)) => f,
                (None, None) => {
                    if self.scheduler.has_pending() {
                        self.now
                    } else {
                        break;
                    }
                }
            };
            self.now = self.now.max(t);

            // 1. completion at this tick.
            if finish_t == Some(self.now) {
                self.complete_inflight();
            }
            // 2. arrivals at this tick.
            while let Some(TraceOp::Arrive(req)) = ops.get(idx).filter(|o| o.at_ns() == self.now) {
                let req = req.clone();
                idx += 1;
                self.arrive(req);
            }
            // 3. cancels at this tick.
            while let Some(TraceOp::Cancel { id, .. }) =
                ops.get(idx).filter(|o| o.at_ns() == self.now)
            {
                let id = id.clone();
                idx += 1;
                self.cancel(&id);
            }
            // 4. dispatch if the device is idle.
            if self.inflight.is_none() {
                if let Some(d) = self.scheduler.next(self.now, self.head) {
                    self.dispatch(d);
                }
            }
        }
        EngineOutput {
            events: self.events,
            outcomes: self.outcomes,
            total_service_ns: self.total_service_ns,
            total_seek_ns: self.total_seek_ns,
            total_seek_distance_sectors: self.total_seek_distance_sectors,
            cancel_rejected: self.cancel_rejected,
            makespan_ns: self.last_completion_ns,
            head_final_sector: self.head,
        }
    }

    fn arrive(&mut self, req: crate::request::BlockRequest) {
        let id = req.id.clone();
        self.push(EventKind::Arrived, vec![id.clone()], String::new());
        for note in self.scheduler.insert(req) {
            self.push(EventKind::Merged, vec![id.clone()], note);
        }
    }

    fn dispatch(&mut self, d: Dispatch) {
        let cost = self
            .device
            .service(self.head, d.item.start, d.item.len);
        let finish_ns = self.now + cost.service_ns;
        self.total_service_ns += cost.service_ns;
        self.total_seek_ns += cost.seek_ns;
        self.total_seek_distance_sectors += cost.seek_distance_sectors;
        let ids = d.item.member_ids();
        self.push(
            EventKind::Dispatched {
                reason: d.reason.clone(),
                head_sector: self.head,
                service_ns: cost.service_ns,
                finish_ns,
            },
            ids,
            format!(
                "range [{}, {}) direction {}",
                d.item.start,
                d.item.end(),
                d.item.direction.as_str()
            ),
        );
        self.inflight = Some(InFlight {
            dispatch: d,
            dispatch_ns: self.now,
            finish_ns,
            service_ns: cost.service_ns,
            cancel_requested: false,
        });
    }

    fn complete_inflight(&mut self) {
        let f = self.inflight.take().expect("inflight");
        self.head = f.dispatch.item.end();
        self.last_completion_ns = self.now;
        let ids = f.dispatch.item.member_ids();
        for member in &f.dispatch.item.members {
            let status = if f.cancel_requested {
                FinalStatus::CancelledAfterDispatch
            } else {
                FinalStatus::Completed
            };
            let deadline_miss = status == FinalStatus::Completed
                && member
                    .deadline_ns
                    .map(|d| self.now > member.arrival_ns + d)
                    .unwrap_or(false);
            let merged_with: Vec<RequestId> = ids
                .iter()
                .filter(|i| *i != &member.id)
                .cloned()
                .collect();
            self.outcomes.push(RequestOutcome {
                id: member.id.clone(),
                status,
                arrival_ns: member.arrival_ns,
                dispatch_ns: Some(f.dispatch_ns),
                finish_ns: self.now,
                wait_ns: Some(f.dispatch_ns - member.arrival_ns),
                service_ns: f.service_ns,
                merged_with,
                deadline_ns: member.deadline_ns,
                deadline_miss,
            });
            let kind = if f.cancel_requested {
                EventKind::CancelledAfterDispatch {
                    service_ns: f.service_ns,
                }
            } else {
                EventKind::Completed {
                    service_ns: f.service_ns,
                }
            };
            self.push(kind, vec![member.id.clone()], String::new());
        }
    }

    fn cancel(&mut self, id: &RequestId) {
        // Case 1: in-flight — cannot be recalled, mark and let it finish.
        if let Some(f) = self.inflight.as_mut() {
            if f.dispatch.item.members.iter().any(|m| &m.id == id) {
                f.cancel_requested = true;
                self.push(
                    EventKind::CancelRequested,
                    vec![id.clone()],
                    "already dispatched; device operation runs to completion and its cost is counted"
                        .to_string(),
                );
                return;
            }
        }
        // Case 2: still queued — removed for free, possibly splitting a merge.
        if let Some(req) = self.scheduler.remove_member(id) {
            self.outcomes.push(RequestOutcome {
                id: req.id.clone(),
                status: FinalStatus::CancelledBeforeDispatch,
                arrival_ns: req.arrival_ns,
                dispatch_ns: None,
                finish_ns: self.now,
                wait_ns: None,
                service_ns: 0,
                merged_with: Vec::new(),
                deadline_ns: req.deadline_ns,
                deadline_miss: false,
            });
            self.push(
                EventKind::CancelledBeforeDispatch,
                vec![id.clone()],
                "removed from scheduler queue; no device time spent".to_string(),
            );
            return;
        }
        // Case 3: not found — classify why.
        let reason = if self.outcomes.iter().any(|o| &o.id == id) {
            CancelRejectReason::AlreadyFinished
        } else {
            CancelRejectReason::UnknownId
        };
        self.cancel_rejected += 1;
        self.push(
            EventKind::CancelRejected { reason },
            vec![id.clone()],
            String::new(),
        );
    }
}
