//! Engine contract tests. A controllable scripted adapter interleaves
//! success, cancel, timeout and handle reuse; every test asserts concrete
//! outcomes and failure categories against hand-written expectations, plus
//! resource invariants (buffer lifecycle, no double-free).

use ioq_runtime::adapter::scripted::{Behavior, ScriptedAdapter};
use ioq_runtime::adapter::{AdapterCompletion, IoAdapter, IoResult};
use ioq_runtime::diag::{Decision, DiagKind};
use ioq_runtime::engine::{CancelAccept, CancelError, Engine, EngineConfig, SubmitError};
use ioq_runtime::handle::{ConnLifecycle, HandleError};
use ioq_runtime::journal::MemJournal;
use ioq_runtime::model::{ConnHandle, FinalOutcome, IoOp, SubmissionId, UserData};

fn cfg() -> EngineConfig {
    EngineConfig {
        queue_capacity: 8,
        buffer_capacity: 8,
        timeout_ms: 50,
        snapshot_every: 2,
    }
}

fn engine() -> Engine {
    Engine::new(cfg(), Box::new(MemJournal::new()))
}

fn read_op(path: &str) -> IoOp {
    IoOp::Read {
        path: path.to_string(),
    }
}

fn submit_ok(e: &mut Engine, h: ConnHandle) -> ioq_runtime::model::UserData {
    e.submit(h, read_op("/data/x.bin"), None)
        .expect("submit accepted")
}

#[test]
fn happy_path_success_exactly_one_record() {
    let mut e = engine();
    let mut a = ScriptedAdapter::with_script(
        vec![Behavior::Succeed {
            delay_ms: 10,
            bytes: 128,
        }],
        Behavior::Never,
    );
    let h = e.open_connection(None);
    let token = submit_ok(&mut e, h);

    e.pump(&mut a); // dispatch
    assert_eq!(a.in_flight(), 1);
    assert!(e.final_record(token.submission).is_none());

    e.advance_time(10);
    e.pump(&mut a);

    let rec = e.final_record(token.submission).expect("final record");
    assert_eq!(rec.outcome, FinalOutcome::Success { bytes: 128 });
    assert!(!rec.cancel_requested);
    assert_eq!(rec.submitted_at_ms, 0);
    assert_eq!(rec.finished_at_ms, 10);
    // Exactly one final record exists for this submission.
    assert_eq!(e.finalized_records().len(), 1);
    // Buffer came back to the pool.
    assert_eq!(e.stats().buffers_leased, 0);
    // No orphans on the happy path.
    assert!(e
        .events()
        .iter()
        .all(|ev| ev.kind != DiagKind::OrphanCompletion));
}

#[test]
fn cancel_before_dispatch_never_touches_device() {
    let mut e = engine();
    let mut a = ScriptedAdapter::new(Behavior::Never);
    let h = e.open_connection(None);
    let token = submit_ok(&mut e, h);

    let accepted = e.cancel(h, token.submission, None).expect("cancel ok");
    assert_eq!(accepted, CancelAccept::Immediate);

    let rec = e.final_record(token.submission).expect("final record");
    assert_eq!(
        rec.outcome,
        FinalOutcome::Cancelled {
            io_performed: false
        }
    );
    e.pump(&mut a);
    assert_eq!(a.in_flight(), 0, "device never saw the submission");
}

#[test]
fn cancel_accepted_but_io_still_completes() {
    // Contract: 取消请求成功 ≠ IO 未发生。
    let mut e = engine();
    let mut a = ScriptedAdapter::with_script(
        vec![Behavior::Succeed {
            delay_ms: 10,
            bytes: 64,
        }],
        Behavior::Never,
    );
    let h = e.open_connection(None);
    let token = submit_ok(&mut e, h);
    e.pump(&mut a); // dispatch

    let accepted = e.cancel(h, token.submission, None).expect("cancel ok");
    assert_eq!(accepted, CancelAccept::Forwarded);
    e.pump(&mut a); // forward cancel to device
    assert_eq!(a.cancel_acks.len(), 1);

    e.advance_time(10);
    e.pump(&mut a);

    // The cancel was accepted, yet the IO completed successfully — and the
    // record says so.
    let rec = e.final_record(token.submission).expect("final record");
    assert_eq!(rec.outcome, FinalOutcome::Success { bytes: 64 });
    assert!(rec.cancel_requested);
    // The diagnostic for the accepted cancel must state the contract.
    let cancel_event = e
        .events()
        .iter()
        .find(|ev| ev.kind == DiagKind::CancelAccepted)
        .expect("cancel accepted event");
    assert!(cancel_event.reason.contains("does NOT mean"));
}

#[test]
fn cancel_honored_reports_whether_io_was_performed() {
    for (performed, expected) in [
        (false, FinalOutcome::Cancelled { io_performed: false }),
        (true, FinalOutcome::Cancelled { io_performed: true }),
    ] {
        let mut e = engine();
        let mut a = ScriptedAdapter::with_script(
            vec![Behavior::HangUntilCancel {
                performed_on_cancel: performed,
            }],
            Behavior::Never,
        );
        let h = e.open_connection(None);
        let token = submit_ok(&mut e, h);
        e.pump(&mut a);
        assert_eq!(
            e.cancel(h, token.submission, None).unwrap(),
            CancelAccept::Forwarded
        );
        e.pump(&mut a); // forward + device completes as Cancelled

        let rec = e.final_record(token.submission).expect("final record");
        assert_eq!(rec.outcome, expected);
        assert!(rec.cancel_requested);
        assert_eq!(e.stats().buffers_leased, 0);
    }
}

#[test]
fn timeout_is_indeterminate_and_late_completion_is_orphan() {
    let mut e = engine();
    let mut a = ScriptedAdapter::with_script(
        vec![Behavior::Succeed {
            delay_ms: 100,
            bytes: 1,
        }],
        Behavior::Never,
    );
    let h = e.open_connection(None);
    let token = submit_ok(&mut e, h);
    e.pump(&mut a);

    e.advance_time(50); // deadline (timeout_ms = 50) reached
    let rec = e.final_record(token.submission).expect("final record");
    assert_eq!(rec.outcome, FinalOutcome::TimedOut);
    // The timeout diagnostic must say the outcome is undecidable.
    let timeout_event = e
        .events()
        .iter()
        .find(|ev| ev.kind == DiagKind::Timeout)
        .expect("timeout event");
    assert_eq!(timeout_event.decision, Decision::Indeterminate);
    // The device still holds the buffer: resource is NOT released.
    assert_eq!(e.stats().buffers_leased, 1);
    // Closing the connection must wait for the outstanding completion.
    let view = e.close_connection(h, None).expect("close accepted");
    assert_eq!(view.lifecycle, ConnLifecycle::Draining);

    // The late completion finally arrives.
    e.advance_time(100);
    e.pump(&mut a);

    // Still exactly one final record, and it is still the timeout.
    assert_eq!(e.finalized_records().len(), 1);
    assert_eq!(
        e.final_record(token.submission).unwrap().outcome,
        FinalOutcome::TimedOut
    );
    // The orphan was logged with a rejection reason.
    let orphan = e
        .events()
        .iter()
        .find(|ev| ev.kind == DiagKind::OrphanCompletion)
        .expect("orphan event");
    assert_eq!(orphan.decision, Decision::Reject);
    assert!(orphan.reason.contains("late completion"));
    // Now the buffer is back and the connection could fully close.
    assert_eq!(e.stats().buffers_leased, 0);
    let view = e.connection_view(token.conn).expect("conn view");
    assert_eq!(view.lifecycle, ConnLifecycle::Closed);
}

#[test]
fn late_completion_cannot_hit_reused_handle() {
    let mut e = engine();
    let mut a = ScriptedAdapter::with_script(
        vec![
            Behavior::Never, // s1: lost, will time out
            Behavior::Succeed {
                delay_ms: 10,
                bytes: 7,
            }, // s2
        ],
        Behavior::Never,
    );
    let h1 = e.open_connection(None);
    let s1 = submit_ok(&mut e, h1);
    e.pump(&mut a);
    e.advance_time(50); // s1 times out; buffer still leased by the device
    assert_eq!(
        e.final_record(s1.submission).unwrap().outcome,
        FinalOutcome::TimedOut
    );
    let view = e.close_connection(h1, None).unwrap();
    assert_eq!(view.lifecycle, ConnLifecycle::Draining);

    // The device finally answers s1: orphan, buffer handed back, conn closes.
    a.inject(AdapterCompletion {
        token: s1,
        result: IoResult::Success { bytes: 1 },
    });
    e.pump(&mut a);
    assert_eq!(e.connection_view(s1.conn).unwrap().lifecycle, ConnLifecycle::Closed);

    // Slot is reused with a bumped generation.
    let h2 = e.open_connection(None);
    assert_eq!(h2.conn, h1.conn, "slot reused");
    assert!(h2.generation.0 > h1.generation.0, "generation bumped");
    let s2 = submit_ok(&mut e, h2);
    e.pump(&mut a); // dispatch s2

    // A confused device replays a completion for s2's submission id but with
    // the OLD generation. It must not finalize or otherwise touch s2.
    a.inject(AdapterCompletion {
        token: UserData {
            conn: s2.conn,
            generation: h1.generation,
            submission: s2.submission,
        },
        result: IoResult::Failed {
            kind: ioq_runtime::model::FailureKind::Io,
        },
    });
    e.pump(&mut a);
    assert!(
        e.final_record(s2.submission).is_none(),
        "stale-generation completion must not finalize the reused handle's record"
    );
    let stale_orphan = e
        .events()
        .iter()
        .filter(|ev| ev.kind == DiagKind::OrphanCompletion)
        .nth(1)
        .expect("stale-generation orphan event");
    assert!(stale_orphan.reason.contains("stale token"));

    // s2 completes normally on its own token.
    e.advance_time(60);
    e.pump(&mut a);
    assert_eq!(
        e.final_record(s2.submission).unwrap().outcome,
        FinalOutcome::Success { bytes: 7 }
    );
    // Exactly one final record per submission, two submissions total.
    assert_eq!(e.finalized_records().len(), 2);
}

#[test]
fn queue_overflow_is_explicit_backpressure() {
    let mut e = Engine::new(
        EngineConfig {
            queue_capacity: 1,
            ..cfg()
        },
        Box::new(MemJournal::new()),
    );
    let h = e.open_connection(None);
    e.submit(h, read_op("/data/a"), None).expect("first fits");
    let second = e.submit(h, read_op("/data/b"), None);
    assert_eq!(second, Err(SubmitError::QueueFull { capacity: 1 }));
    let rejection = e
        .events()
        .iter()
        .find(|ev| ev.kind == DiagKind::SubmissionRejected)
        .expect("rejection event");
    assert_eq!(rejection.decision, Decision::Reject);
    assert!(rejection.reason.contains("backpressure"));
}

#[test]
fn buffer_exhaustion_defers_dispatch_until_release() {
    let mut e = Engine::new(
        EngineConfig {
            buffer_capacity: 1,
            ..cfg()
        },
        Box::new(MemJournal::new()),
    );
    let mut a = ScriptedAdapter::with_script(
        vec![
            Behavior::Succeed {
                delay_ms: 10,
                bytes: 1,
            },
            Behavior::Succeed {
                delay_ms: 0,
                bytes: 2,
            },
        ],
        Behavior::Never,
    );
    let h = e.open_connection(None);
    let s1 = submit_ok(&mut e, h);
    let s2 = submit_ok(&mut e, h);

    e.pump(&mut a); // s1 dispatched, s2 deferred (pool exhausted)
    assert_eq!(a.in_flight(), 1);
    assert_eq!(e.stats().queued, 1);
    assert!(e
        .events()
        .iter()
        .any(|ev| ev.kind == DiagKind::DispatchDeferred
            && ev.decision == Decision::Indeterminate));

    e.advance_time(10);
    e.pump(&mut a); // s1 completes, buffer returns (dispatch ran before poll)
    e.pump(&mut a); // s2 dispatched and completes (delay 0)
    assert_eq!(
        e.final_record(s1.submission).unwrap().outcome,
        FinalOutcome::Success { bytes: 1 }
    );
    assert_eq!(
        e.final_record(s2.submission).unwrap().outcome,
        FinalOutcome::Success { bytes: 2 }
    );
    assert_eq!(e.stats().buffers_leased, 0);
}

#[test]
fn connection_close_waits_for_all_completions() {
    let mut e = engine();
    let mut a = ScriptedAdapter::with_script(
        vec![Behavior::Succeed {
            delay_ms: 20,
            bytes: 3,
        }],
        Behavior::Never,
    );
    let h1 = e.open_connection(None);
    let s1 = submit_ok(&mut e, h1);
    e.pump(&mut a);

    let view = e.close_connection(h1, None).unwrap();
    assert_eq!(view.lifecycle, ConnLifecycle::Draining);
    // While draining, the slot is not reusable.
    let h_other = e.open_connection(None);
    assert_ne!(h_other.conn, h1.conn);

    e.advance_time(20);
    e.pump(&mut a);
    assert_eq!(
        e.final_record(s1.submission).unwrap().outcome,
        FinalOutcome::Success { bytes: 3 }
    );
    assert_eq!(e.connection_view(h1.conn).unwrap().lifecycle, ConnLifecycle::Closed);

    // Now the slot is reusable, with a bumped generation.
    let h2 = e.open_connection(None);
    assert_eq!(h2.conn, h1.conn);
    assert_eq!(h2.generation.0, h1.generation.0 + 1);
}

#[test]
fn double_completion_is_detected_without_double_free() {
    let mut e = engine();
    let mut a = ScriptedAdapter::with_script(
        vec![Behavior::DoubleComplete {
            delay_ms: 5,
            bytes: 7,
        }],
        Behavior::Never,
    );
    let h = e.open_connection(None);
    let token = submit_ok(&mut e, h);
    e.pump(&mut a);
    e.advance_time(5);
    e.pump(&mut a); // device emits the completion twice

    // Exactly one final record.
    assert_eq!(e.finalized_records().len(), 1);
    assert_eq!(
        e.final_record(token.submission).unwrap().outcome,
        FinalOutcome::Success { bytes: 7 }
    );
    // The duplicate was logged as an orphan.
    assert_eq!(
        e.events()
            .iter()
            .filter(|ev| ev.kind == DiagKind::OrphanCompletion)
            .count(),
        1
    );
    // No double-free: the buffer was released exactly once and the registry
    // never observed an invalid release.
    assert_eq!(e.stats().buffers_leased, 0);
    assert!(e
        .events()
        .iter()
        .all(|ev| ev.kind != DiagKind::BufferReleaseRejected));
}

#[test]
fn cancel_after_final_is_rejected_with_outcome() {
    let mut e = engine();
    let mut a = ScriptedAdapter::with_script(
        vec![Behavior::Succeed {
            delay_ms: 0,
            bytes: 9,
        }],
        Behavior::Never,
    );
    let h = e.open_connection(None);
    let token = submit_ok(&mut e, h);
    e.pump(&mut a);

    let err = e.cancel(h, token.submission, None).unwrap_err();
    assert_eq!(
        err,
        CancelError::AlreadyFinal(FinalOutcome::Success { bytes: 9 })
    );
    let rejection = e
        .events()
        .iter()
        .find(|ev| ev.kind == DiagKind::CancelRejected)
        .expect("cancel rejected event");
    assert_eq!(rejection.decision, Decision::Reject);
}

#[test]
fn submit_with_stale_handle_is_rejected() {
    let mut e = engine();
    let h1 = e.open_connection(None);
    e.close_connection(h1, None).unwrap(); // no pending: closes immediately
    let h2 = e.open_connection(None);
    assert_eq!(h2.conn, h1.conn);
    assert_eq!(h2.generation.0, 2);

    let err = e.submit(h1, read_op("/data/a"), None).unwrap_err();
    assert_eq!(
        err,
        SubmitError::StaleHandle(HandleError::StaleGeneration {
            expected: 2,
            found: 1
        })
    );
}

#[test]
fn journal_matches_handwritten_oracle() {
    // Scenario driven end-to-end; the journal (JSONL, parsed independently
    // with serde_json) must match expectations written by hand below —
    // not derived from the engine under test.
    let mut e = engine(); // timeout 50, snapshot_every 2
    let mut a = ScriptedAdapter::with_script(
        vec![
            Behavior::Succeed {
                delay_ms: 5,
                bytes: 10,
            }, // s1
            Behavior::HangUntilCancel {
                performed_on_cancel: true,
            }, // s2
            Behavior::Succeed {
                delay_ms: 100,
                bytes: 30,
            }, // s3: too slow, times out
        ],
        Behavior::Never,
    );
    let h = e.open_connection(None);
    let s1 = submit_ok(&mut e, h);
    let s2 = submit_ok(&mut e, h);
    let s3 = submit_ok(&mut e, h);

    e.pump(&mut a); // t=0: dispatch all three
    e.advance_time(1);
    assert_eq!(
        e.cancel(h, s2.submission, None).unwrap(),
        CancelAccept::Forwarded
    );
    e.pump(&mut a); // t=1: forward cancel; s2 completes as Cancelled
    e.advance_time(5);
    e.pump(&mut a); // t=5: s1 succeeds
    e.advance_time(50); // t=50: s3 times out
    e.advance_time(100);
    e.pump(&mut a); // t=100: s3's late completion -> orphan, buffer back

    // Hand-written oracle: (submission id, outcome type, extra check).
    let expected: Vec<(u64, &str, bool)> = vec![
        (s2.submission.0, "cancelled", true),  // io_performed = true
        (s1.submission.0, "success", false),   // bytes = 10
        (s3.submission.0, "timed_out", false), // late completion is orphan
    ];

    let lines: Vec<serde_json::Value> = e
        .journal_lines()
        .expect("mem journal")
        .iter()
        .map(|l| serde_json::from_str(l).expect("valid JSONL"))
        .collect();
    let completions: Vec<&serde_json::Value> = lines
        .iter()
        .filter(|l| l["type"] == "completion")
        .collect();
    assert_eq!(completions.len(), expected.len(), "exactly one record each");
    for (line, (sid, outcome, io_performed)) in completions.iter().zip(expected.iter()) {
        assert_eq!(line["payload"]["user_data"]["submission"], *sid);
        assert_eq!(line["payload"]["outcome"]["type"], *outcome);
        if *io_performed {
            assert_eq!(line["payload"]["outcome"]["io_performed"], true);
            assert_eq!(line["payload"]["cancel_requested"], true);
        }
    }
    assert_eq!(completions[1]["payload"]["outcome"]["bytes"], 10);
    // Sampled state: snapshot_every = 2 -> one snapshot after 2 completions.
    let snapshots: Vec<&serde_json::Value> =
        lines.iter().filter(|l| l["type"] == "snapshot").collect();
    assert_eq!(snapshots.len(), 1);
    assert_eq!(snapshots[0]["payload"]["finalized_total"], 2);
    // The late completion left no second record and freed the buffer.
    assert_eq!(e.stats().orphan_completions, 1);
    assert_eq!(e.stats().buffers_leased, 0);
    assert_eq!(e.finalized_records().len(), 3);
}

#[test]
fn lost_io_keeps_buffer_leased_forever() {
    // Behavior::Never models a lost request: the record times out but the
    // buffer can never be reclaimed — visible in stats as a permanent lease.
    let mut e = engine();
    let mut a = ScriptedAdapter::new(Behavior::Never);
    let h = e.open_connection(None);
    let token = submit_ok(&mut e, h);
    e.pump(&mut a);
    e.advance_time(50);
    assert_eq!(
        e.final_record(token.submission).unwrap().outcome,
        FinalOutcome::TimedOut
    );
    assert_eq!(e.stats().buffers_leased, 1);
    let view = e.close_connection(h, None).unwrap();
    assert_eq!(
        view.lifecycle,
        ConnLifecycle::Draining,
        "connection can never fully close while the device holds the buffer"
    );
    e.advance_time(10_000);
    e.pump(&mut a);
    assert_eq!(e.stats().buffers_leased, 1);
    assert_eq!(e.connection_view(h.conn).unwrap().lifecycle, ConnLifecycle::Draining);
}

#[test]
fn unknown_submission_cancel_is_rejected() {
    let mut e = engine();
    let h = e.open_connection(None);
    let err = e.cancel(h, SubmissionId(999), None).unwrap_err();
    assert_eq!(err, CancelError::UnknownSubmission);
}
