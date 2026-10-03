//! Contract tests: completion attribution, cancellation semantics, timeouts
//! and handle reuse. All expected values are hardcoded in the tests — the
//! ScriptedAdapter only delivers events the test explicitly scripts.

use io_queue_runtime::adapter::scripted::ScriptedAdapter;
use io_queue_runtime::adapter::{AdapterEvent, CancelAck};
use io_queue_runtime::model::{
    CompletionOutcome, Generation, Handle, OpKind, RecordId, Submission,
};
use io_queue_runtime::runtime::{CancelError, CoreEvent, RuntimeCore};

fn core(capacity: usize) -> RuntimeCore<ScriptedAdapter> {
    RuntimeCore::new(capacity, capacity, 1_000, ScriptedAdapter::new())
}

fn submit_op(user_data: u64, op: OpKind, timeout_ms: u64) -> Submission {
    Submission {
        user_data,
        op,
        timeout_ms,
    }
}

fn nop(user_data: u64) -> Submission {
    submit_op(user_data, OpKind::Nop, 1_000)
}

fn finalized(events: &[CoreEvent]) -> Vec<&io_queue_runtime::model::CompletionRecord> {
    events
        .iter()
        .filter_map(|e| match e {
            CoreEvent::Finalized(r) => Some(r),
            _ => None,
        })
        .collect()
}

#[test]
fn success_completion_is_attributed_to_its_user_data() {
    let mut core = core(4);
    let accepted = core.submit(nop(0xAAAA)).expect("submit");
    assert_eq!(accepted.record_id, RecordId(1));
    assert_eq!(accepted.handle.slot, 0);
    assert_eq!(accepted.handle.generation, Generation(0));

    core.adapter_mut().push_event(AdapterEvent::Completed {
        slot: 0,
        generation: Generation(0),
        result: Ok(7),
    });
    let events = core.poll(10);

    let records = finalized(&events);
    assert_eq!(records.len(), 1, "exactly one completion record");
    let record = records[0];
    assert_eq!(record.record_id, RecordId(1));
    assert_eq!(record.user_data, 0xAAAA, "user_data bound to this record");
    assert_eq!(record.outcome, CompletionOutcome::Success { bytes: 7 });
    assert!(!record.cancel_requested);
    assert_eq!(core.in_flight(), 0);
}

#[test]
fn cancel_accepted_but_io_completes_anyway() {
    let mut core = core(4);
    let accepted = core.submit(nop(0xBBBB)).expect("submit");

    // Cancel is accepted...
    let cancel = core.cancel(accepted.handle).expect("cancel accepted");
    assert_eq!(cancel.record_id, RecordId(1));
    assert_eq!(cancel.ack, CancelAck::WillCancel);

    // ...but the backend finishes the IO before the cancel takes effect.
    core.adapter_mut().push_event(AdapterEvent::Completed {
        slot: accepted.handle.slot,
        generation: accepted.handle.generation,
        result: Ok(3),
    });
    let events = core.poll(10);

    let records = finalized(&events);
    assert_eq!(records.len(), 1, "still exactly one final record");
    assert_eq!(
        records[0].outcome,
        CompletionOutcome::Success { bytes: 3 },
        "cancel accepted != IO did not happen"
    );
    assert!(records[0].cancel_requested, "record notes the cancel attempt");
}

#[test]
fn cancel_confirmed_by_adapter_finalizes_as_cancelled() {
    let mut core = core(4);
    let accepted = core.submit(nop(0xCCCC)).expect("submit");
    core.cancel(accepted.handle).expect("cancel accepted");

    core.adapter_mut().push_event(AdapterEvent::Cancelled {
        slot: accepted.handle.slot,
        generation: accepted.handle.generation,
    });
    let events = core.poll(10);

    let records = finalized(&events);
    assert_eq!(records.len(), 1);
    assert_eq!(records[0].outcome, CompletionOutcome::Cancelled);
    assert!(records[0].cancel_requested);
}

#[test]
fn timeout_finalizes_record_and_aborts_adapter_op() {
    let mut core = core(4);
    let accepted = core.submit(submit_op(0xDDDD, OpKind::Nop, 100)).expect("submit");

    // Adapter never answers. At t=150 the deadline (100) has passed.
    let events = core.poll(150);

    let records = finalized(&events);
    assert_eq!(records.len(), 1);
    assert_eq!(records[0].record_id, accepted.record_id);
    assert_eq!(records[0].outcome, CompletionOutcome::TimedOut);
    assert_eq!(records[0].completed_at_ms, 150);
    assert_eq!(
        core.adapter().aborts,
        vec![(accepted.handle.slot, accepted.handle.generation)],
        "adapter told to clean up the timed-out op"
    );
}

#[test]
fn late_completion_cannot_hit_reused_handle() {
    let mut core = core(1); // single slot forces reuse
    let first = core.submit(nop(0x1111)).expect("submit first");
    core.adapter_mut().push_event(AdapterEvent::Completed {
        slot: first.handle.slot,
        generation: first.handle.generation,
        result: Ok(1),
    });
    core.poll(10);

    // Slot is reused by a new submission with a bumped generation.
    let second = core.submit(nop(0x2222)).expect("submit second");
    assert_eq!(second.handle.slot, first.handle.slot, "slot reused");
    assert_eq!(
        second.handle.generation,
        Generation(first.handle.generation.0 + 1),
        "generation bumped"
    );

    // The backend now delivers a *late* duplicate of the first completion,
    // still carrying the old generation.
    core.adapter_mut().push_event(AdapterEvent::Completed {
        slot: first.handle.slot,
        generation: first.handle.generation,
        result: Ok(999),
    });
    let events = core.poll(20);

    assert!(
        finalized(&events).is_empty(),
        "late completion finalizes nothing"
    );
    assert!(
        events.iter().any(|e| matches!(
            e,
            CoreEvent::Anomaly(
                io_queue_runtime::runtime::Anomaly::LateCompletionStaleGeneration {
                    slot: 0,
                    event_generation: 0,
                    current_generation: 1,
                }
            )
        )),
        "stale event recorded as anomaly: {events:?}"
    );
    assert_eq!(core.in_flight(), 1, "second submission untouched");

    // The second submission completes with its own generation.
    core.adapter_mut().push_event(AdapterEvent::Completed {
        slot: second.handle.slot,
        generation: second.handle.generation,
        result: Ok(2),
    });
    let events = core.poll(30);
    let records = finalized(&events);
    assert_eq!(records.len(), 1);
    assert_eq!(records[0].record_id, RecordId(2));
    assert_eq!(records[0].user_data, 0x2222);
    assert_eq!(records[0].outcome, CompletionOutcome::Success { bytes: 2 });

    // And the first record is still intact and singular.
    let all: Vec<_> = core.recent_records().iter().collect();
    assert_eq!(all.len(), 2);
    assert_eq!(all[0].record_id, RecordId(1));
    assert_eq!(all[0].outcome, CompletionOutcome::Success { bytes: 1 });
}

#[test]
fn duplicate_completion_is_an_anomaly_not_a_second_record() {
    let mut core = core(4);
    let accepted = core.submit(nop(0x3333)).expect("submit");
    let event = AdapterEvent::Completed {
        slot: accepted.handle.slot,
        generation: accepted.handle.generation,
        result: Ok(5),
    };
    core.adapter_mut().push_event(event.clone());
    core.adapter_mut().push_event(event);
    let events = core.poll(10);

    assert_eq!(finalized(&events).len(), 1, "exactly one record");
    assert!(
        events.iter().any(|e| matches!(
            e,
            CoreEvent::Anomaly(
                io_queue_runtime::runtime::Anomaly::CompletionForIdleSlot { slot: 0, .. }
            )
        )),
        "duplicate recorded as anomaly: {events:?}"
    );
    assert_eq!(core.anomalies().len(), 1);
}

#[test]
fn cancel_failure_categories_are_distinct() {
    let mut core = core(2);
    let accepted = core.submit(nop(0x4444)).expect("submit");

    // Unknown slot.
    let err = core
        .cancel(Handle {
            slot: 9,
            generation: Generation(0),
        })
        .expect_err("unknown slot rejected");
    assert_eq!(err, CancelError::UnknownHandle { slot: 9 });

    // Stale generation on an existing slot.
    let err = core
        .cancel(Handle {
            slot: accepted.handle.slot,
            generation: Generation(7),
        })
        .expect_err("stale generation rejected");
    assert_eq!(
        err,
        CancelError::StaleGeneration {
            slot: 0,
            got: 7,
            current: 0,
        }
    );

    // Existing slot with matching generation but nothing in flight.
    let err = core
        .cancel(Handle {
            slot: 1,
            generation: Generation(0),
        })
        .expect_err("idle slot rejected");
    assert_eq!(err, CancelError::NotInFlight { slot: 1 });

    // Double cancel.
    core.cancel(accepted.handle).expect("first cancel");
    let err = core
        .cancel(accepted.handle)
        .expect_err("second cancel rejected");
    assert_eq!(
        err,
        CancelError::AlreadyRequested {
            record_id: accepted.record_id,
        }
    );

    // After finalization the old handle is stale.
    core.adapter_mut().push_event(AdapterEvent::Completed {
        slot: accepted.handle.slot,
        generation: accepted.handle.generation,
        result: Ok(0),
    });
    core.poll(10);
    let err = core
        .cancel(accepted.handle)
        .expect_err("cancel after finalize rejected");
    assert_eq!(
        err,
        CancelError::StaleGeneration {
            slot: 0,
            got: 0,
            current: 1,
        }
    );
}

#[test]
fn decision_log_explains_acceptance_and_rejection() {
    let mut core = core(1);
    core.submit(nop(0x5555)).expect("submit");
    core.submit(nop(0x6666)).expect_err("full queue rejected");

    let decisions: Vec<_> = core.decisions().iter().collect();
    let accept = decisions
        .iter()
        .find(|d| d.action == "SubmitAccepted")
        .expect("accept decision logged");
    assert_eq!(accept.record_id, Some(1));
    assert!(accept.reason.contains("slot 0"), "reason names the slot");
    let reject = decisions
        .iter()
        .find(|d| d.action == "SubmitRejectedQueueFull")
        .expect("reject decision logged");
    assert!(reject.reason.contains("1/1"), "reason explains capacity");
}
