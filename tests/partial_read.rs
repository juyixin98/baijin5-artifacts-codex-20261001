//! 部分读取失败：stat 读取失败的进程不能当作已消失；真正消失的才退出。

mod common;

use common::*;
use proc_diff::model::{DeltaCategory, ExitReason, FailureCategory};

#[test]
fn partial_read_failure_keeps_process_alive() {
    let (engine, expected) = run_fixture("partial-read");

    // pid 600 在 seq 2 读取失败：不退出、不产生 delta。
    assert!(engine.exits().iter().all(|e| e.identity.pid != 600));
    let d600 = engine.deltas_for(600);
    assert_eq!(d600.len(), 2, "first observation + recovery delta, nothing for seq 2");

    // pid 700 完全缺席且非读取失败：判定退出。
    assert_eq!(engine.exits().len(), 1);
    assert_eq!(engine.exits()[0].identity, id(700, 1));
    assert_eq!(engine.exits()[0].reason, ExitReason::Exited);

    // 读取失败区间进入不可确定报告。
    let und = engine.undetermined();
    assert_eq!(und.len(), 1);
    assert_eq!(und[0].identity, id(600, 1));
    assert_eq!(und[0].category, FailureCategory::ReadFailure);
    assert!(!und[0].delta_known);
    assert_eq!((und[0].from_seq, und[0].to_seq), (1, 2));

    // seq 3 恢复读取：增量跨 seq 1 -> 3 仍精确，(80+80)-(50+50) = 60。
    assert_eq!(d600[1].category, DeltaCategory::Ok);
    assert_eq!(d600[1].delta, Some(60));
    assert_eq!((d600[1].from_seq, d600[1].to_seq), (Some(1), 3));

    assert_fixture_matches(&engine, &expected);
}
