//! Diagnostics: request ids, redacted keys, and the accept / reject /
//! undecidable decision taxonomy.

mod common;

use arc_cache::diag::{Decision, Op};
use arc_cache::engine::{Engine, Request};
use arc_cache::error::ErrorCategory;

use common::{never_fail, pair};

fn submit(engine: &mut Engine, request_id: &str, op: Op, page: u64, data: Option<Vec<u8>>) {
    engine.submit(Request {
        request_id: Some(request_id.into()),
        op,
        page,
        data,
    });
}

#[test]
fn every_request_produces_a_redacted_record_with_reason() {
    let (mut engine, _model, store) = pair(2, 8, never_fail());

    submit(&mut engine, "req-accepted", Op::Read, 1, None);
    submit(&mut engine, "req-rejected", Op::Read, 99, None); // absent from store
    store.set_fail_loads(true);
    submit(&mut engine, "req-undecidable", Op::Read, 2, None);
    store.set_fail_loads(false);
    submit(&mut engine, "req-bad", Op::Write, 3, None); // write without payload

    let records = engine.diagnostics(10, None);
    assert_eq!(records.len(), 4);

    let accepted = records[0];
    assert_eq!(accepted.request_id, "req-accepted");
    assert!(matches!(accepted.decision, Decision::Accepted { .. }));
    assert!(!accepted.reason.is_empty());

    let rejected = records[1];
    assert!(matches!(
        rejected.decision,
        Decision::Rejected {
            category: ErrorCategory::NotFound,
            ..
        }
    ));

    let undecidable = records[2];
    assert!(matches!(undecidable.decision, Decision::Undecidable { .. }));

    let bad = records[3];
    assert!(matches!(
        bad.decision,
        Decision::Rejected {
            category: ErrorCategory::BadRequest,
            ..
        }
    ));

    // Keys are redacted by default: no raw page id anywhere in the record.
    for record in &records {
        let key = record.key.clone().expect("page ops carry a key ref");
        assert!(key.starts_with("pg#"), "expected redacted key, got {key}");
        assert!(!key.contains("99"), "raw page id leaked into diagnostics");
    }

    // Filtering by decision kind works.
    assert_eq!(engine.diagnostics(10, Some("accepted")).len(), 1);
    assert_eq!(engine.diagnostics(10, Some("rejected")).len(), 2);
    assert_eq!(engine.diagnostics(10, Some("undecidable")).len(), 1);
}

#[test]
fn raw_key_logging_is_opt_in() {
    let store = arc_cache::store::MemStore::new();
    store.put(7, b"seven".to_vec());
    let wb = common::ScriptedWriteback {
        store: store.clone(),
        fail: never_fail(),
    };
    let mut engine = Engine::new(
        2,
        Box::new(store),
        Box::new(wb),
        64,
        8,
        true, // log_raw_keys
    );
    submit(&mut engine, "raw", Op::Read, 7, None);
    let records = engine.diagnostics(1, None);
    assert_eq!(records[0].key.as_deref(), Some("page:7"));
}

#[test]
fn generated_request_ids_are_unique() {
    let (mut engine, _model, _store) = pair(2, 8, never_fail());
    let r1 = engine.submit(Request {
        request_id: None,
        op: Op::Read,
        page: 1,
        data: None,
    });
    let r2 = engine.submit(Request {
        request_id: None,
        op: Op::Read,
        page: 1,
        data: None,
    });
    assert_ne!(r1.request_id, r2.request_id);
    assert!(r1.request_id.starts_with("req-"));
}
