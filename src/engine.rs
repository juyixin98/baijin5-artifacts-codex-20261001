//! The run-model state machine.
//!
//! The engine is deliberately synchronous and clock-injected: tests drive
//! `advance_time`/`pump` by hand to interleave success, cancel, timeout and
//! handle reuse deterministically, while the server drives the same methods
//! from a timer. All concurrency-sensitive invariants live here:
//!
//! 1. A `UserData` token is bound to exactly one submission, forever.
//! 2. An accepted cancel request does not imply the IO did not happen.
//! 3. Exactly one final completion record per submission; late completions
//!    become orphan diagnostics and can never claim a reused handle.
//! 4. Queue overflow and buffer exhaustion are explicit backpressure.
//! 5. Connection resources are released only after every associated
//!    completion is final and every leased buffer has come back.

use std::collections::HashMap;

use serde::Serialize;

use crate::adapter::{AdapterCompletion, IoAdapter, IoResult};
use crate::buffer::{BufferError, BufferRegistry};
use crate::diag::{redact_op, Decision, DiagEvent, DiagKind};
use crate::handle::{ConnLifecycle, ConnectionTable, HandleError};
use crate::journal::{Journal, JournalSink};
use crate::model::{
    BufferId, CompletionRecord, ConnHandle, ConnId, FinalOutcome, IoOp, RecordPhase, RecordView,
    SubmissionId, UserData,
};
use crate::queue::SubmissionQueue;

#[derive(Debug, Clone)]
pub struct EngineConfig {
    pub queue_capacity: usize,
    pub buffer_capacity: usize,
    pub timeout_ms: u64,
    pub snapshot_every: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SubmitError {
    StaleHandle(HandleError),
    QueueFull { capacity: usize },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CancelError {
    StaleHandle(HandleError),
    UnknownSubmission,
    AlreadyFinal(FinalOutcome),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CancelAccept {
    /// Finalized immediately: the IO never reached the device.
    Immediate,
    /// Forwarded to the device; the IO may still complete.
    Forwarded,
}

#[derive(Debug, Clone, Serialize)]
pub struct Stats {
    pub now_ms: u64,
    pub open_connections: usize,
    pub queued: usize,
    pub in_flight: usize,
    pub finalized_total: u64,
    pub orphan_completions: u64,
    pub timeouts: u64,
    pub cancels_forwarded: u64,
    pub buffers_leased: usize,
    pub buffers_capacity: usize,
    pub queue_capacity: usize,
}

#[derive(Debug)]
struct Record {
    user_data: UserData,
    op: IoOp,
    phase: RecordPhase,
    cancel_requested: bool,
    submitted_at_ms: u64,
    deadline_ms: u64,
    buffer: Option<BufferId>,
    final_record: Option<CompletionRecord>,
}

pub struct Engine {
    cfg: EngineConfig,
    now_ms: u64,
    conns: ConnectionTable,
    records: HashMap<SubmissionId, Record>,
    queue: SubmissionQueue,
    buffers: BufferRegistry,
    pending_cancels: Vec<UserData>,
    next_submission: u64,
    event_seq: u64,
    events: Vec<DiagEvent>,
    journal: Journal,
    finalized: Vec<CompletionRecord>,
    orphan_count: u64,
    timeout_count: u64,
    cancels_forwarded: u64,
}

impl Engine {
    pub fn new(cfg: EngineConfig, sink: Box<dyn JournalSink>) -> Self {
        Self {
            queue: SubmissionQueue::new(cfg.queue_capacity),
            buffers: BufferRegistry::new(cfg.buffer_capacity),
            journal: Journal::new(sink, cfg.snapshot_every),
            conns: ConnectionTable::new(),
            records: HashMap::new(),
            pending_cancels: Vec::new(),
            next_submission: 0,
            event_seq: 0,
            events: Vec::new(),
            finalized: Vec::new(),
            orphan_count: 0,
            timeout_count: 0,
            cancels_forwarded: 0,
            now_ms: 0,
            cfg,
        }
    }

    // ---- connections -------------------------------------------------

    pub fn open_connection(&mut self, request_id: Option<String>) -> ConnHandle {
        let handle = self.conns.open();
        self.emit(
            DiagKind::ConnectionOpened,
            Decision::Accept,
            None,
            request_id,
            format!(
                "connection {} opened with generation {}",
                handle.conn, handle.generation
            ),
        );
        handle
    }

    pub fn connection_view(&self, conn: ConnId) -> Option<crate::handle::ConnView> {
        self.conns.view(conn)
    }

    pub fn close_connection(
        &mut self,
        handle: ConnHandle,
        request_id: Option<String>,
    ) -> Result<crate::handle::ConnView, HandleError> {
        self.conns.validate(handle)?;
        let view = self.conns.begin_close(handle.conn);
        match view.lifecycle {
            ConnLifecycle::Closed => self.emit(
                DiagKind::ConnectionClosed,
                Decision::Accept,
                None,
                request_id,
                format!(
                    "connection {} closed immediately: no pending completions, no leased buffers",
                    handle.conn
                ),
            ),
            _ => self.emit(
                DiagKind::ConnectionCloseRequested,
                Decision::Accept,
                None,
                request_id,
                format!(
                    "connection {} draining: {} pending record(s), {} leased buffer(s); \
                     resource release waits for all associated completions",
                    handle.conn, view.pending_records, view.outstanding_buffers
                ),
            ),
        }
        Ok(view)
    }

    // ---- submissions ---------------------------------------------------

    pub fn submit(
        &mut self,
        handle: ConnHandle,
        op: IoOp,
        request_id: Option<String>,
    ) -> Result<UserData, SubmitError> {
        if let Err(e) = self.conns.validate_open(handle) {
            self.emit(
                DiagKind::SubmissionRejected,
                Decision::Reject,
                None,
                request_id,
                format!("rejected: {e}"),
            );
            return Err(SubmitError::StaleHandle(e));
        }
        if self.queue.is_full() {
            let capacity = self.cfg.queue_capacity;
            self.emit(
                DiagKind::SubmissionRejected,
                Decision::Reject,
                None,
                request_id,
                format!(
                    "submission queue full (capacity {capacity}); \
                     explicit backpressure — caller must retry later"
                ),
            );
            return Err(SubmitError::QueueFull { capacity });
        }
        let sid = SubmissionId(self.next_submission);
        self.next_submission += 1;
        let token = UserData {
            conn: handle.conn,
            generation: handle.generation,
            submission: sid,
        };
        let record = Record {
            user_data: token,
            op: op.clone(),
            phase: RecordPhase::Submitted,
            cancel_requested: false,
            submitted_at_ms: self.now_ms,
            deadline_ms: self.now_ms + self.cfg.timeout_ms,
            buffer: None,
            final_record: None,
        };
        self.records.insert(sid, record);
        self.queue.push_back(sid).expect("checked not full");
        self.conns.on_submit(handle.conn);
        self.emit(
            DiagKind::SubmissionAccepted,
            Decision::Accept,
            Some(token),
            request_id,
            format!(
                "queued (depth {}); op {}; token bound to this submission only",
                self.queue.len(),
                redact_op(&op)
            ),
        );
        Ok(token)
    }

    pub fn cancel(
        &mut self,
        handle: ConnHandle,
        sid: SubmissionId,
        request_id: Option<String>,
    ) -> Result<CancelAccept, CancelError> {
        if let Err(e) = self.conns.validate(handle) {
            self.emit(
                DiagKind::CancelRejected,
                Decision::Reject,
                None,
                request_id,
                format!("cancel rejected: {e}"),
            );
            return Err(CancelError::StaleHandle(e));
        }
        let Some(record) = self.records.get(&sid) else {
            self.emit(
                DiagKind::CancelRejected,
                Decision::Reject,
                None,
                request_id,
                format!("cancel rejected: unknown submission {sid}"),
            );
            return Err(CancelError::UnknownSubmission);
        };
        let token = record.user_data;
        if token.conn != handle.conn || token.generation != handle.generation {
            self.emit(
                DiagKind::CancelRejected,
                Decision::Reject,
                Some(token),
                request_id,
                "cancel rejected: submission does not belong to this connection handle"
                    .to_string(),
            );
            return Err(CancelError::StaleHandle(HandleError::NotOwner));
        }
        let phase = record.phase;
        let final_outcome = record.final_record.as_ref().map(|f| f.outcome);
        if let Some(outcome) = final_outcome {
            self.emit(
                DiagKind::CancelRejected,
                Decision::Reject,
                Some(token),
                request_id,
                format!("cancel rejected: submission already final ({outcome:?})"),
            );
            return Err(CancelError::AlreadyFinal(outcome));
        }
        match phase {
            RecordPhase::Submitted => {
                self.queue.remove(sid);
                self.finalize(sid, FinalOutcome::Cancelled {
                    io_performed: false,
                });
                self.emit(
                    DiagKind::CancelAccepted,
                    Decision::Accept,
                    Some(token),
                    request_id,
                    "cancelled before dispatch; the IO never reached the device".to_string(),
                );
                Ok(CancelAccept::Immediate)
            }
            _ => {
                if let Some(record) = self.records.get_mut(&sid) {
                    record.cancel_requested = true;
                    record.phase = RecordPhase::CancelRequested;
                }
                self.pending_cancels.push(token);
                self.emit(
                    DiagKind::CancelAccepted,
                    Decision::Accept,
                    Some(token),
                    request_id,
                    "cancel accepted and will be forwarded to the device; this does NOT mean \
                     the IO did not happen — the final completion record is authoritative"
                        .to_string(),
                );
                Ok(CancelAccept::Forwarded)
            }
        }
    }

    // ---- driving the model ----------------------------------------------

    pub fn advance_time(&mut self, now_ms: u64) {
        self.now_ms = now_ms;
        let expired: Vec<SubmissionId> = self
            .records
            .iter()
            .filter(|(_, r)| {
                r.final_record.is_none()
                    && matches!(r.phase, RecordPhase::InFlight | RecordPhase::CancelRequested)
                    && r.deadline_ms <= now_ms
            })
            .map(|(id, _)| *id)
            .collect();
        for sid in expired {
            let token = self.records[&sid].user_data;
            let deadline = self.records[&sid].deadline_ms;
            self.emit(
                DiagKind::Timeout,
                Decision::Indeterminate,
                Some(token),
                None,
                format!(
                    "deadline {deadline}ms reached with no completion; IO outcome unknown — \
                     a late completion will be logged as an orphan and cannot create \
                     a second record"
                ),
            );
            self.finalize(sid, FinalOutcome::TimedOut);
        }
    }

    pub fn pump(&mut self, adapter: &mut dyn IoAdapter) {
        // 1. Forward pending cancels. The ack is informational only.
        for token in std::mem::take(&mut self.pending_cancels) {
            let ack = adapter.cancel(token);
            self.cancels_forwarded += 1;
            self.emit(
                DiagKind::CancelForwarded,
                Decision::Accept,
                Some(token),
                None,
                format!(
                    "cancel forwarded to device (ack: {ack:?}); acknowledgement is not \
                     an outcome — the IO may still complete"
                ),
            );
        }
        // 2. Dispatch queued submissions while buffers are available.
        loop {
            let Some(sid) = self.queue.front() else { break };
            let Some(record) = self.records.get(&sid) else {
                self.queue.pop_front();
                continue;
            };
            if record.phase != RecordPhase::Submitted {
                self.queue.pop_front();
                continue;
            }
            let token = record.user_data;
            match self.buffers.lease(sid) {
                Ok(buf) => {
                    self.queue.pop_front();
                    let op = record.op.clone();
                    if let Some(record) = self.records.get_mut(&sid) {
                        record.phase = RecordPhase::InFlight;
                        record.buffer = Some(buf);
                    }
                    self.conns.on_buffer_lease(token.conn);
                    adapter.start(token, &op, self.now_ms);
                    self.emit(
                        DiagKind::Dispatch,
                        Decision::Accept,
                        Some(token),
                        None,
                        format!("dispatched to device with buffer {buf}"),
                    );
                }
                Err(BufferError::Exhausted) => {
                    self.emit(
                        DiagKind::DispatchDeferred,
                        Decision::Indeterminate,
                        Some(token),
                        None,
                        "buffer pool exhausted; dispatch deferred (resource backpressure)"
                            .to_string(),
                    );
                    break;
                }
                Err(e) => unreachable!("lease only fails with Exhausted, got {e}"),
            }
        }
        // 3. Collect device completions.
        for completion in adapter.poll(self.now_ms) {
            self.route_completion(completion);
        }
    }

    fn route_completion(&mut self, completion: AdapterCompletion) {
        let sid = completion.token.submission;
        let Some(record) = self.records.get(&sid) else {
            self.orphan_count += 1;
            self.emit(
                DiagKind::OrphanCompletion,
                Decision::Reject,
                Some(completion.token),
                None,
                "completion for unknown submission; dropped".to_string(),
            );
            return;
        };
        let expected = record.user_data;
        let is_final = record.final_record.is_some();
        if expected != completion.token {
            self.orphan_count += 1;
            self.emit(
                DiagKind::OrphanCompletion,
                Decision::Reject,
                Some(completion.token),
                None,
                format!(
                    "stale token: generation {} does not match record generation {}; \
                     the handle was reused and this completion cannot claim the new owner",
                    completion.token.generation, expected.generation
                ),
            );
            return;
        }
        if is_final {
            self.orphan_count += 1;
            self.emit(
                DiagKind::OrphanCompletion,
                Decision::Reject,
                Some(completion.token),
                None,
                "late completion after the final record; exactly-one record preserved"
                    .to_string(),
            );
            // The device may still hold the buffer (e.g. after a timeout);
            // its completion is what hands the buffer back.
            self.release_buffer_for(sid);
            return;
        }
        let outcome = match completion.result {
            IoResult::Success { bytes } => FinalOutcome::Success { bytes },
            IoResult::Failed { kind } => FinalOutcome::Failed { kind },
            IoResult::Cancelled { io_performed } => FinalOutcome::Cancelled { io_performed },
        };
        self.release_buffer_for(sid);
        self.finalize(sid, outcome);
    }

    fn release_buffer_for(&mut self, sid: SubmissionId) {
        let Some(record) = self.records.get(&sid) else {
            return;
        };
        let Some(buf) = record.buffer else {
            return;
        };
        let token = record.user_data;
        match self.buffers.release(buf, sid) {
            Ok(()) => {
                if let Some(record) = self.records.get_mut(&sid) {
                    record.buffer = None;
                }
                self.conns.on_buffer_release(token.conn);
                self.emit(
                    DiagKind::BufferReleased,
                    Decision::Accept,
                    Some(token),
                    None,
                    format!("buffer {buf} returned to pool"),
                );
                self.maybe_free_connection(token.conn);
            }
            Err(e) => {
                self.emit(
                    DiagKind::BufferReleaseRejected,
                    Decision::Reject,
                    Some(token),
                    None,
                    format!("buffer release rejected, registry unchanged: {e}"),
                );
            }
        }
    }

    fn finalize(&mut self, sid: SubmissionId, outcome: FinalOutcome) {
        let (token, cancel_requested, submitted_at_ms) = {
            let record = self.records.get(&sid).expect("record exists");
            debug_assert!(
                record.final_record.is_none(),
                "exactly-once violation: finalize called twice for {sid}"
            );
            (
                record.user_data,
                record.cancel_requested,
                record.submitted_at_ms,
            )
        };
        let completion = CompletionRecord {
            user_data: token,
            outcome,
            cancel_requested,
            submitted_at_ms,
            finished_at_ms: self.now_ms,
        };
        if let Some(record) = self.records.get_mut(&sid) {
            record.phase = RecordPhase::Final;
            record.final_record = Some(completion.clone());
        }
        self.conns.on_finalize(token.conn);
        if matches!(outcome, FinalOutcome::TimedOut) {
            self.timeout_count += 1;
        }
        self.finalized.push(completion.clone());
        self.journal.record_completion(&completion);
        let stats = self.stats();
        self.journal.maybe_snapshot(&stats);
        self.emit(
            DiagKind::CompletionFinalized,
            Decision::Accept,
            Some(token),
            None,
            format!("final outcome recorded exactly once: {outcome:?}"),
        );
        self.maybe_free_connection(token.conn);
    }

    fn maybe_free_connection(&mut self, conn: ConnId) {
        if self.conns.maybe_free(conn) {
            self.emit(
                DiagKind::ConnectionClosed,
                Decision::Accept,
                None,
                None,
                format!(
                    "connection {conn} fully drained: all completions final, all buffers \
                     released; slot may be reused with a new generation"
                ),
            );
        }
    }

    // ---- inspection ------------------------------------------------------

    pub fn record_view(&self, sid: SubmissionId) -> Option<RecordView> {
        self.records.get(&sid).map(|r| RecordView {
            user_data: r.user_data,
            phase: r.phase,
            cancel_requested: r.cancel_requested,
            submitted_at_ms: r.submitted_at_ms,
            deadline_ms: r.deadline_ms,
            final_record: r.final_record.clone(),
        })
    }

    pub fn final_record(&self, sid: SubmissionId) -> Option<CompletionRecord> {
        self.records.get(&sid).and_then(|r| r.final_record.clone())
    }

    pub fn finalized_records(&self) -> &[CompletionRecord] {
        &self.finalized
    }

    pub fn events(&self) -> &[DiagEvent] {
        &self.events
    }

    pub fn journal_lines(&self) -> Option<&[String]> {
        self.journal.lines()
    }

    pub fn stats(&self) -> Stats {
        Stats {
            now_ms: self.now_ms,
            open_connections: self.conns.open_count(),
            queued: self.queue.len(),
            in_flight: self
                .records
                .values()
                .filter(|r| matches!(r.phase, RecordPhase::InFlight | RecordPhase::CancelRequested))
                .count(),
            finalized_total: self.finalized.len() as u64,
            orphan_completions: self.orphan_count,
            timeouts: self.timeout_count,
            cancels_forwarded: self.cancels_forwarded,
            buffers_leased: self.buffers.leased_count(),
            buffers_capacity: self.cfg.buffer_capacity,
            queue_capacity: self.cfg.queue_capacity,
        }
    }

    fn emit(
        &mut self,
        kind: DiagKind,
        decision: Decision,
        subject: Option<UserData>,
        request_id: Option<String>,
        reason: String,
    ) {
        self.event_seq += 1;
        self.events.push(DiagEvent {
            seq: self.event_seq,
            ts_ms: self.now_ms,
            request_id,
            subject,
            kind,
            decision,
            reason,
        });
    }
}
