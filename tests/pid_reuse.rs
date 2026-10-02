//! PID 快速复用：同一 PID 以新启动代次出现时，不得继承旧计数。

mod common;

use common::*;
use proc_diff::model::{DeltaCategory, ExitReason};

#[test]
fn reused_pid_starts_fresh_identity() {
    let (engine, expected) = run_fixture("pid-reuse");

    // pid 100 appears as two distinct identities.
    let deltas = engine.deltas_for(100);
    assert_eq!(deltas.len(), 2, "expected one delta record per identity");
    assert_eq!(deltas[0].identity, id(100, 5));
    assert_eq!(deltas[0].category, DeltaCategory::FirstObservation);
    assert_eq!(deltas[1].identity, id(100, 9));
    assert_eq!(deltas[1].category, DeltaCategory::IdentityReset);
    // 关键：新代次的 delta 为 None，而不是 5 - 50 之类的继承/回退值。
    assert_eq!(deltas[1].delta, None);

    // 旧身份以 pid_reused 退出，退出时刻与第二份快照对齐。
    assert_eq!(engine.exits().len(), 1);
    let exit = &engine.exits()[0];
    assert_eq!(exit.identity, id(100, 5));
    assert_eq!(exit.reason, ExitReason::PidReused);
    assert_eq!((exit.seq, exit.at_ms), (2, 2000));

    // 未复用的 pid 1 正常出增量： (20+5) - (10+5) = 10。
    let d1 = engine.deltas_for(1);
    assert_eq!(d1[1].delta, Some(10));
    assert_eq!(d1[1].category, DeltaCategory::Ok);

    // 无不可确定区间。
    assert!(engine.undetermined().is_empty());

    assert_fixture_matches(&engine, &expected);
}
