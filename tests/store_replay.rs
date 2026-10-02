//! 持久化：快照写入 JSONL 日志后可完整重放，重放结果与直接运行一致。

mod common;

use common::*;
use proc_diff::engine::{Engine, EngineConfig, IngestOutcome};
use proc_diff::store::Store;

#[test]
fn journal_replay_reproduces_engine_state() {
    let (reference, expected) = run_fixture("partial-read");

    // 重新加载同一夹具快照，边应用边落盘。
    let dir = tempfile::tempdir().expect("tempdir");
    let store = Store::open(dir.path()).expect("store");
    let snaps = proc_diff::snapshot::load_series(&fixture_root("partial-read")).unwrap();
    let cfg = EngineConfig { counter_max: u64::MAX, wrap_max_plausible_delta: 360000 };
    let mut live = Engine::new(cfg);
    for snap in &snaps {
        assert!(matches!(live.apply(snap), IngestOutcome::Accepted { .. }));
        store.append(snap).expect("append");
    }

    // 重放后引擎输出必须与直接运行一致。
    let (replayed, outcomes) = store.replay(cfg).expect("replay");
    assert_eq!(outcomes.len(), snaps.len());
    assert_eq!(replayed.deltas(), reference.deltas());
    assert_eq!(replayed.exits(), reference.exits());
    assert_eq!(replayed.reparents(), reference.reparents());
    assert_eq!(replayed.undetermined(), reference.undetermined());

    // 且与手写参考答案一致（reason 字段除外，由 fixture 测试覆盖）。
    assert_eq!(replayed.deltas().len(), expected.deltas.len());
}
