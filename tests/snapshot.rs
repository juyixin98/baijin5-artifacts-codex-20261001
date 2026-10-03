//! Snapshot persistence: dirty pages are flushed through the write-back
//! adapter before capture; restore rebuilds resident pages from the backing
//! store. Page content is never serialized into the snapshot.

mod common;

use arc_cache::diag::Op;
use arc_cache::engine::{Engine, Request};
use arc_cache::error::ErrorCategory;
use arc_cache::store::MemStore;

use common::{never_fail, pair, ScriptedWriteback};

fn write(engine: &mut Engine, page: u64, data: Vec<u8>) {
    engine
        .submit(Request {
            request_id: None,
            op: Op::Write,
            page,
            data: Some(data),
        })
        .result
        .expect("write");
}

fn read(engine: &mut Engine, page: u64) -> Vec<u8> {
    engine
        .submit(Request {
            request_id: None,
            op: Op::Read,
            page,
            data: None,
        })
        .result
        .expect("read")
        .data
        .expect("read returns data")
}

#[test]
fn snapshot_flushes_dirty_pages_and_restores_state() {
    let (mut engine, _model, store) = pair(4, 8, never_fail());
    // Warm pages 1,2 into T2 and dirty page 3.
    for page in [1u64, 2, 1, 2] {
        read(&mut engine, page);
    }
    write(&mut engine, 3, vec![0xD3; 8]);
    assert_eq!(engine.cache().dirty_pages(), vec![3]);

    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("snap.json");
    engine.snapshot(&path).expect("snapshot");

    // The dirty page was written back; the snapshot holds metadata only.
    assert_eq!(store.get(3), Some(vec![0xD3; 8]));
    assert_eq!(engine.cache().dirty_pages(), Vec::<u64>::new());
    let text = std::fs::read_to_string(&path).unwrap();
    assert!(text.contains("\"p\""));
    assert!(!text.contains("211"), "page content must not be serialized");

    // Restore into the same engine and verify state and content.
    engine.restore(&path).expect("restore");
    let cache = engine.cache();
    assert_eq!(cache.lists().t2, vec![1, 2]);
    assert_eq!(cache.lists().t1, vec![3]);
    assert_eq!(read(&mut engine, 3), vec![0xD3; 8]);
}

#[test]
fn restore_fails_when_resident_page_missing_from_store() {
    let (mut engine, _model, store) = pair(2, 8, never_fail());
    read(&mut engine, 1);
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("snap.json");
    engine.snapshot(&path).expect("snapshot");

    // Remove the page from the backing store: restore must fail with a
    // snapshot-category error, not panic or silently drop the page.
    let store2 = MemStore::new();
    for (id, data) in store.entries() {
        if id != 1 {
            store2.put(id, data);
        }
    }
    let wb = ScriptedWriteback {
        store: store2.clone(),
        fail: never_fail(),
    };
    let mut engine2 = Engine::new(2, Box::new(store2), Box::new(wb), 64, 8, false);
    let err = engine2.restore(&path).expect_err("restore must fail");
    assert_eq!(err.category(), ErrorCategory::Snapshot);
    assert!(err.to_string().contains("page 1"));
}
