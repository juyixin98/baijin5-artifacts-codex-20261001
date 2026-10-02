//! 孤儿重挂：父进程退出后子进程被重挂到 init，时间关系必须保留。

mod common;

use common::*;
use proc_diff::model::{DeltaCategory, ExitReason};

#[test]
fn orphan_reattach_preserves_time_relations() {
    let (engine, expected) = run_fixture("orphan-reparent");

    // 父进程 200 在 seq 2 退出。
    assert_eq!(engine.exits().len(), 1);
    let exit = &engine.exits()[0];
    assert_eq!(exit.identity, id(200, 2));
    assert_eq!(exit.reason, ExitReason::Exited);

    // 子进程 300 在同一采样点被重挂 200 -> 1，事件带时间戳。
    assert_eq!(engine.reparents().len(), 1);
    let reparent = &engine.reparents()[0];
    assert_eq!(reparent.identity, id(300, 3));
    assert_eq!((reparent.old_ppid, reparent.new_ppid), (200, 1));
    // 时间关系：重挂与父退出发生在同一采样点。
    assert_eq!((reparent.seq, reparent.at_ms), (exit.seq, exit.at_ms));

    // 进程树：300 现在是 1 的孩子；200 已不在树中。
    let tree = engine.tree();
    let init = tree.nodes.iter().find(|n| n.identity == id(1, 1)).expect("init node");
    assert_eq!(init.children, vec![id(300, 3)]);
    assert!(!tree.nodes.iter().any(|n| n.identity.pid == 200));
    let orphan = tree.nodes.iter().find(|n| n.identity == id(300, 3)).expect("child node");
    assert_eq!(orphan.ppid, 1);

    // 重挂不影响 CPU 增量：(8+6) - (5+5) = 4。
    let d300 = engine.deltas_for(300);
    assert_eq!(d300[1].delta, Some(4));
    assert_eq!(d300[1].category, DeltaCategory::Ok);

    assert_fixture_matches(&engine, &expected);
}
