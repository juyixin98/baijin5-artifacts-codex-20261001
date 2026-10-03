//! Backpressure and buffer-lifecycle tests: queue overflow is an explicit
//! rejection, buffers are leased/freed exactly once, and resource release
//! waits for all associated completions.

use io_queue_runtime::adapter::scripted::ScriptedAdapter;
use io_queue_runtime::adapter::AdapterEvent;
use io_queue_runtime::model::{OpKind, RecordId, Submission};
use io_queue_runtime::runtime::{CoreEvent, ReleaseBlocked, RuntimeCore, SubmitError};
use std::path::PathBuf;

fn read_submission(user_data: u64) -> Submission {
    Submission {
        user_data,
        op: OpKind::ReadFile {
            path: PathBuf::from("/tmp/fixture.bin"),
        },
        timeout_ms: 1_000,
    }
}

#[test]
fn queue_overflow_is_explicit_backpressure() {
    let mut core = RuntimeCore::new(2, 2, 1_000, ScriptedAdapter::new());
    core.submit(read_submission(1)).expect("first");
    core.submit(read_submission(2)).expect("second");

    let err = core.submit(read_submission(3)).expect_err("third rejected");
    assert_eq!(
        err,
        SubmitError::QueueFull {
            in_flight: 2,
            capacity: 2,
        },
        "overflow is a typed QueueFull, not silent growth"
    );

    // After one completion, capacity opens up again.
    core.adapter_mut().push_event(AdapterEvent::Completed {
        slot: 0,
        generation: io_queue_runtime::model::Generation(0),
        result: Ok(10),
    });
    core.poll(10);
    core.submit(read_submission(4)).expect("space after completion");
}

#[test]
fn buffer_exhaustion_is_a_distinct_backpressure_category() {
    // Two slots but only one buffer: the second read cannot lease.
    let mut core = RuntimeCore::new(2, 1, 1_000, ScriptedAdapter::new());
    core.submit(read_submission(1)).expect("first leases the buffer");
    let err = core.submit(read_submission(2)).expect_err("no buffer left");
    assert_eq!(
        err,
        SubmitError::NoBuffersAvailable { in_flight: 1 },
        "buffer exhaustion is reported separately from queue overflow"
    );
}

#[test]
fn buffer_leased_and_freed_exactly_once_per_record() {
    let mut core = RuntimeCore::new(2, 2, 1_000, ScriptedAdapter::new());
    assert_eq!(core.buffer_stats().free, 2);

    core.submit(read_submission(1)).expect("submit");
    assert_eq!(core.buffer_stats().leased, 1, "buffer leased for the record");

    core.adapter_mut().push_event(AdapterEvent::Completed {
        slot: 0,
        generation: io_queue_runtime::model::Generation(0),
        result: Ok(4),
    });
    let events = core.poll(10);
    assert_eq!(core.buffer_stats().free, 2, "buffer returned on finalize");
    assert!(
        !events
            .iter()
            .any(|e| matches!(e, CoreEvent::Anomaly(_))),
        "no buffer anomaly on the happy path: {events:?}"
    );
}

#[test]
fn nop_operations_do_not_lease_buffers() {
    let mut core = RuntimeCore::new(2, 1, 1_000, ScriptedAdapter::new());
    core.submit(Submission {
        user_data: 1,
        op: OpKind::Nop,
        timeout_ms: 1_000,
    })
    .expect("nop submit");
    assert_eq!(core.buffer_stats().leased, 0, "Nop needs no buffer");
}

#[test]
fn resource_release_waits_for_all_completions() {
    let mut core = RuntimeCore::new(2, 2, 1_000, ScriptedAdapter::new());
    let a = core.submit(read_submission(1)).expect("submit a");
    let b = core.submit(read_submission(2)).expect("submit b");

    // Release while both are in flight: blocked, naming both records.
    let err = core.try_release().expect_err("release blocked");
    assert_eq!(
        err,
        ReleaseBlocked::InFlight(vec![RecordId(1), RecordId(2)])
    );

    // One completion is not enough.
    core.adapter_mut().push_event(AdapterEvent::Completed {
        slot: a.handle.slot,
        generation: a.handle.generation,
        result: Ok(1),
    });
    core.poll(10);
    let err = core.try_release().expect_err("still blocked");
    assert_eq!(err, ReleaseBlocked::InFlight(vec![RecordId(2)]));

    // After the last completion, release succeeds and further leases fail.
    core.adapter_mut().push_event(AdapterEvent::Completed {
        slot: b.handle.slot,
        generation: b.handle.generation,
        result: Ok(1),
    });
    core.poll(20);
    core.try_release().expect("release after all completions");
    assert!(core.buffer_stats().released);
}
