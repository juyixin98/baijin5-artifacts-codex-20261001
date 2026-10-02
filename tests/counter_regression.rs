//! 计数回绕与数据异常：累计 CPU 回退必须区分为“回绕”与“异常”。

mod common;

use common::*;
use proc_diff::model::{DeltaCategory, FailureCategory};

#[test]
fn counter_regression_split_into_wrap_and_anomaly() {
    let (engine, expected) = run_fixture("counter-regression");

    // pid 400: 950 -> 30，counter_max=1000，回绕增量 = 1000-950+30+1 = 81。
    let d400 = engine.deltas_for(400);
    assert_eq!(d400[1].category, DeltaCategory::CounterWrap);
    assert_eq!(d400[1].delta, Some(81));

    // pid 500: 800 -> 100，若按回绕需 301 > 阈值 200，判为数据异常。
    let d500 = engine.deltas_for(500);
    assert_eq!(d500[1].category, DeltaCategory::CounterAnomaly);
    assert_eq!(d500[1].delta, None, "anomaly interval must not fabricate a delta");

    // 异常区间进入不可确定报告；回绕不进。
    let und = engine.undetermined();
    assert_eq!(und.len(), 1);
    assert_eq!(und[0].identity, id(500, 1));
    assert_eq!(und[0].category, FailureCategory::CounterAnomaly);
    assert!(!und[0].delta_known);
    assert_eq!((und[0].from_seq, und[0].to_seq), (1, 2));

    assert_fixture_matches(&engine, &expected);
}
