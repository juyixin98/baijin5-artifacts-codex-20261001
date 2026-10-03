//! Runtime core: the run model that ties slots, buffers and the adapter
//! together and enforces the business contracts:
//!
//! - `user_data` is bound to exactly one `RecordId` at submit time;
//! - every record finalizes exactly once — late or duplicate completions
//!   become anomalies, never second records;
//! - a cancel request is only a *request*: the IO may still complete;
//! - a full queue rejects submissions with explicit backpressure;
//! - resource release waits for all associated completions.
//!
//! The core is synchronous and uses a logical clock; an outer driver (async
//! task in the demo binary, direct calls in tests) feeds it time and polls
//! the adapter.

use crate::adapter::{AdapterEvent, CancelAck, IoAdapter};
use crate::model::{
    CompletionOutcome, CompletionRecord, Handle, Millis, RecordId, Submission,
};
use crate::resources::{BufferRegistry, BufferStats, ResourceError};
use crate::ring::{ActiveRecord, SlotTable, SlotView};
use serde::Serialize;
use std::collections::VecDeque;

/// How many finalized records / decisions are kept in memory for diagnostics.
const RECENT_WINDOW: usize = 256;

/// Failure categories for `submit`.
#[derive(Clone, Debug, PartialEq, Eq, thiserror::Error)]
pub enum SubmitError {
    /// Queue is at capacity. This *is* the backpressure signal.
    #[error("queue full: {in_flight}/{capacity} slots in flight")]
    QueueFull { in_flight: usize, capacity: usize },
    /// Slot available but no data buffer left to lease.
    #[error("no buffers available for {in_flight} in-flight record(s)")]
    NoBuffersAvailable { in_flight: usize },
}

/// Failure categories for `cancel`.
#[derive(Clone, Debug, PartialEq, Eq, thiserror::Error)]
pub enum CancelError {
    #[error("unknown handle: slot {slot} does not exist")]
    UnknownHandle { slot: u16 },
    #[error("stale generation for slot {slot}: handle has {got}, current is {current}")]
    StaleGeneration { slot: u16, got: u32, current: u32 },
    #[error("slot {slot} exists but has no in-flight record")]
    NotInFlight { slot: u16 },
    #[error("cancel already requested for record {}", record_id.0)]
    AlreadyRequested { record_id: RecordId },
}

/// Returned when a cancel request is accepted. Acceptance means the runtime
/// will *try* to cancel; the operation may still complete.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CancelAccepted {
    pub record_id: RecordId,
    pub ack: CancelAck,
}

/// Returned when a submission is accepted: the fresh record identity plus
/// the opaque handle the caller uses for cancellation.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SubmitAccepted {
    pub record_id: RecordId,
    pub handle: Handle,
}

/// Events emitted by [`RuntimeCore::poll`] for the driver to persist.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CoreEvent {
    /// A record reached its single terminal state.
    Finalized(CompletionRecord),
    /// Something impossible-by-contract was observed and rejected.
    Anomaly(Anomaly),
}

/// Contract violations observed at the adapter boundary. Always journaled
/// (never sampled away) because they indicate a misbehaving backend.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub enum Anomaly {
    /// Event carried a generation that does not match the slot's current
    /// occupant — a late completion from a previous handle.
    LateCompletionStaleGeneration {
        slot: u16,
        event_generation: u32,
        current_generation: u32,
    },
    /// Event arrived for a slot with no in-flight record (e.g. a duplicate
    /// completion after the record already finalized).
    CompletionForIdleSlot { slot: u16, event_generation: u32 },
    /// The buffer registry reported an impossible transition.
    BufferError { record_id: RecordId, message: String },
}

/// One entry in the decision log: why the runtime accepted, rejected or
/// could not decide something. Diagnostics expose these verbatim.
#[derive(Clone, Debug, Serialize)]
pub struct Decision {
    pub seq: u64,
    pub record_id: Option<u64>,
    pub action: &'static str,
    pub reason: String,
}

/// Why a release attempt failed.
#[derive(Clone, Debug, PartialEq, Eq, thiserror::Error)]
pub enum ReleaseBlocked {
    #[error("release blocked: {} record(s) still in flight: {:?}", .0.len(), .0)]
    InFlight(Vec<RecordId>),
}

pub struct RuntimeCore<A: IoAdapter> {
    slots: SlotTable,
    buffers: BufferRegistry,
    adapter: A,
    default_timeout_ms: Millis,
    now_ms: Millis,
    next_record_id: u64,
    next_decision_seq: u64,
    records: VecDeque<CompletionRecord>,
    decisions: VecDeque<Decision>,
    anomalies: Vec<Anomaly>,
}

impl<A: IoAdapter> RuntimeCore<A> {
    pub fn new(
        capacity: usize,
        buffer_count: usize,
        default_timeout_ms: Millis,
        adapter: A,
    ) -> Self {
        Self {
            slots: SlotTable::new(capacity),
            buffers: BufferRegistry::new(buffer_count),
            adapter,
            default_timeout_ms,
            now_ms: 0,
            next_record_id: 1,
            next_decision_seq: 1,
            records: VecDeque::new(),
            decisions: VecDeque::new(),
            anomalies: Vec::new(),
        }
    }

    pub fn adapter(&self) -> &A {
        &self.adapter
    }

    pub fn adapter_mut(&mut self) -> &mut A {
        &mut self.adapter
    }

    pub fn in_flight(&self) -> usize {
        self.slots.in_flight()
    }

    pub fn capacity(&self) -> usize {
        self.slots.capacity()
    }

    pub fn buffer_stats(&self) -> BufferStats {
        self.buffers.stats()
    }

    pub fn slot_snapshot(&self) -> Vec<SlotView> {
        self.slots.snapshot()
    }

    pub fn recent_records(&self) -> &VecDeque<CompletionRecord> {
        &self.records
    }

    pub fn find_record(&self, id: RecordId) -> Option<&CompletionRecord> {
        self.records.iter().find(|r| r.record_id == id)
    }

    pub fn decisions(&self) -> &VecDeque<Decision> {
        &self.decisions
    }

    pub fn anomalies(&self) -> &[Anomaly] {
        &self.anomalies
    }

    fn log_decision(&mut self, record_id: Option<RecordId>, action: &'static str, reason: String) {
        let decision = Decision {
            seq: self.next_decision_seq,
            record_id: record_id.map(|r| r.0),
            action,
            reason,
        };
        self.next_decision_seq += 1;
        if self.decisions.len() >= RECENT_WINDOW {
            self.decisions.pop_front();
        }
        self.decisions.push_back(decision);
    }

    /// Enqueue one submission. Binds `user_data` to a fresh `RecordId`,
    /// leases a buffer if the op needs one, and starts the adapter.
    pub fn submit(&mut self, submission: Submission) -> Result<SubmitAccepted, SubmitError> {
        let timeout_ms = if submission.timeout_ms == 0 {
            self.default_timeout_ms
        } else {
            submission.timeout_ms
        };

        // Backpressure: the queue is bounded; overflow is an explicit,
        // typed rejection — never silent growth.
        if self.slots.in_flight() >= self.slots.capacity() {
            let err = SubmitError::QueueFull {
                in_flight: self.slots.in_flight(),
                capacity: self.slots.capacity(),
            };
            self.log_decision(
                None,
                "SubmitRejectedQueueFull",
                format!(
                    "queue full ({}/{} slots in flight); user_data redacted",
                    self.slots.in_flight(),
                    self.slots.capacity()
                ),
            );
            return Err(err);
        }

        let record_id = RecordId(self.next_record_id);
        self.next_record_id += 1;

        let buffer = if submission.op.needs_buffer() {
            match self.buffers.lease(record_id) {
                Ok(id) => Some(id),
                Err(ResourceError::Exhausted) => {
                    self.log_decision(
                        Some(record_id),
                        "SubmitRejectedNoBuffer",
                        "buffer pool exhausted; submission rejected as backpressure".to_string(),
                    );
                    return Err(SubmitError::NoBuffersAvailable {
                        in_flight: self.slots.in_flight(),
                    });
                }
                Err(other) => {
                    self.log_decision(
                        Some(record_id),
                        "SubmitRejectedNoBuffer",
                        format!("buffer lease failed: {other}"),
                    );
                    return Err(SubmitError::NoBuffersAvailable {
                        in_flight: self.slots.in_flight(),
                    });
                }
            }
        } else {
            None
        };

        let record = ActiveRecord {
            record_id,
            user_data: submission.user_data,
            op: submission.op.clone(),
            deadline_ms: self.now_ms.saturating_add(timeout_ms),
            submitted_at_ms: self.now_ms,
            cancel_requested: false,
            buffer,
        };
        let handle = self
            .slots
            .alloc(record)
            .expect("capacity checked above; slot must be available");

        self.adapter
            .start(handle.slot, handle.generation, &submission.op);
        self.log_decision(
            Some(record_id),
            "SubmitAccepted",
            format!(
                "slot {} generation {} allocated; op {}; buffer {:?}",
                handle.slot,
                handle.generation.0,
                submission.op.name(),
                buffer
            ),
        );
        Ok(SubmitAccepted { record_id, handle })
    }

    /// Request cancellation of an in-flight record.
    ///
    /// `Ok(CancelAccepted)` only means the request was forwarded to the
    /// adapter. The record may still finalize as `Success` — cancellation
    /// and completion race, and whichever terminal event lands first wins.
    pub fn cancel(&mut self, handle: Handle) -> Result<CancelAccepted, CancelError> {
        let current = match self.slots.current_generation(handle.slot) {
            Some(gen) => gen,
            None => {
                let err = CancelError::UnknownHandle { slot: handle.slot };
                self.log_decision(
                    None,
                    "CancelRejected",
                    format!("slot {} does not exist", handle.slot),
                );
                return Err(err);
            }
        };
        if current != handle.generation.0 {
            let err = CancelError::StaleGeneration {
                slot: handle.slot,
                got: handle.generation.0,
                current,
            };
            self.log_decision(
                None,
                "CancelRejected",
                format!(
                    "stale generation for slot {}: handle has {}, current is {}",
                    handle.slot, handle.generation.0, current
                ),
            );
            return Err(err);
        }
        let active = match self.slots.get_active_mut(handle) {
            Some(active) => active,
            None => {
                // Generation matches but nothing is in flight: the slot is
                // simply idle (no record was ever submitted on this
                // generation, or it finalized without the generation being
                // consumed yet).
                let err = CancelError::NotInFlight { slot: handle.slot };
                self.log_decision(
                    None,
                    "CancelRejected",
                    format!("slot {} has no in-flight record", handle.slot),
                );
                return Err(err);
            }
        };
        if active.cancel_requested {
            let record_id = active.record_id;
            self.log_decision(
                Some(record_id),
                "CancelRejected",
                format!("cancel already requested for record {}", record_id.0),
            );
            return Err(CancelError::AlreadyRequested { record_id });
        }
        active.cancel_requested = true;
        let record_id = active.record_id;
        let ack = self.adapter.cancel(handle.slot, handle.generation);
        self.log_decision(
            Some(record_id),
            "CancelAccepted",
            format!(
                "cancel request forwarded to adapter (ack: {ack:?}); \
                 the IO may still complete — acceptance is not proof of non-execution"
            ),
        );
        Ok(CancelAccepted { record_id, ack })
    }

    /// Advance the clock, drain adapter events, and finalize expired records.
    /// Returns everything the driver should persist.
    pub fn poll(&mut self, now_ms: Millis) -> Vec<CoreEvent> {
        self.now_ms = now_ms;
        let mut out = Vec::new();

        for event in self.adapter.poll() {
            match event {
                AdapterEvent::Completed {
                    slot,
                    generation,
                    result,
                } => {
                    let outcome = match result {
                        Ok(bytes) => CompletionOutcome::Success { bytes },
                        Err(message) => CompletionOutcome::Failed { message },
                    };
                    self.attributable_finalize(slot, generation, outcome, &mut out);
                }
                AdapterEvent::Cancelled { slot, generation } => {
                    self.attributable_finalize(slot, generation, CompletionOutcome::Cancelled, &mut out);
                }
            }
        }

        // Timeout sweep: a deadline that passes with no adapter event
        // finalizes the record as TimedOut. The adapter is told to abort;
        // any event it still emits afterwards will be rejected as stale.
        for handle in self.slots.expired_handles(now_ms) {
            let record_id = self
                .slots
                .get_active(handle)
                .map(|a| a.record_id)
                .expect("expired_handles only returns active handles");
            self.log_decision(
                Some(record_id),
                "TimeoutFinalized",
                format!(
                    "deadline {} ms reached at {} ms with no adapter event",
                    self.slots.get_active(handle).map(|a| a.deadline_ms).unwrap_or(0),
                    now_ms
                ),
            );
            self.adapter.abort(handle.slot, handle.generation);
            self.finalize(handle, CompletionOutcome::TimedOut, &mut out);
        }

        out
    }

    /// Attribute an adapter event to its record, or reject it as an anomaly.
    fn attributable_finalize(
        &mut self,
        slot: u16,
        generation: crate::model::Generation,
        outcome: CompletionOutcome,
        out: &mut Vec<CoreEvent>,
    ) {
        let handle = Handle { slot, generation };
        if !self.slots.is_active(slot) {
            let anomaly = Anomaly::CompletionForIdleSlot {
                slot,
                event_generation: generation.0,
            };
            self.log_decision(
                None,
                "CompletionRejectedIdleSlot",
                format!(
                    "event for slot {} (generation {}) but no record is in flight; \
                     dropped to preserve exactly-once finalization",
                    slot, generation.0
                ),
            );
            self.anomalies.push(anomaly.clone());
            out.push(CoreEvent::Anomaly(anomaly));
            return;
        }
        let current = self
            .slots
            .current_generation(slot)
            .expect("active slot exists");
        if current != generation.0 {
            let anomaly = Anomaly::LateCompletionStaleGeneration {
                slot,
                event_generation: generation.0,
                current_generation: current,
            };
            self.log_decision(
                None,
                "CompletionRejectedStaleGeneration",
                format!(
                    "event for slot {} carries generation {} but current occupant is \
                     generation {}; late completion cannot hit the reused handle",
                    slot, generation.0, current
                ),
            );
            self.anomalies.push(anomaly.clone());
            out.push(CoreEvent::Anomaly(anomaly));
            return;
        }
        self.finalize(handle, outcome, out);
    }

    /// The single place records reach a terminal state. Exactly-once is
    /// structural: the slot's record is removed here, so no later event can
    /// find it again.
    fn finalize(
        &mut self,
        handle: Handle,
        outcome: CompletionOutcome,
        out: &mut Vec<CoreEvent>,
    ) {
        let active = self
            .slots
            .finalize(handle)
            .expect("finalize called with a validated handle");
        if let Some(buffer) = active.buffer {
            if let Err(err) = self.buffers.complete(buffer) {
                let anomaly = Anomaly::BufferError {
                    record_id: active.record_id,
                    message: format!("completing lease {buffer:?} failed: {err}"),
                };
                self.anomalies.push(anomaly.clone());
                out.push(CoreEvent::Anomaly(anomaly));
            }
        }
        let record = CompletionRecord {
            record_id: active.record_id,
            handle,
            user_data: active.user_data,
            op: active.op,
            cancel_requested: active.cancel_requested,
            submitted_at_ms: active.submitted_at_ms,
            completed_at_ms: self.now_ms,
            outcome,
        };
        self.log_decision(
            Some(record.record_id),
            "CompletionAccepted",
            format!(
                "record {} finalized as {} (cancel_requested={})",
                record.record_id.0,
                record.outcome.kind(),
                record.cancel_requested
            ),
        );
        if self.records.len() >= RECENT_WINDOW {
            self.records.pop_front();
        }
        self.records.push_back(record.clone());
        out.push(CoreEvent::Finalized(record));
    }

    /// Attempt to release all resources. Succeeds only when every record has
    /// finalized — resource release waits for all associated completions.
    pub fn try_release(&mut self) -> Result<(), ReleaseBlocked> {
        match self.buffers.release_all() {
            Ok(()) => {
                self.log_decision(None, "ReleaseCompleted", "all buffers released".to_string());
                Ok(())
            }
            Err(ResourceError::OutstandingLeases(ids)) => {
                self.log_decision(
                    None,
                    "ReleaseBlocked",
                    format!(
                        "release refused: {} record(s) still in flight: {:?}",
                        ids.len(),
                        ids.iter().map(|r| r.0).collect::<Vec<_>>()
                    ),
                );
                Err(ReleaseBlocked::InFlight(ids))
            }
            Err(other) => {
                self.log_decision(
                    None,
                    "ReleaseBlocked",
                    format!("release failed: {other}"),
                );
                Err(ReleaseBlocked::InFlight(self.buffers.outstanding()))
            }
        }
    }
}
