//! 采样缺失：seq 2 -> 4 跳号，跨缺口的区间必须被报告为不可完全确定。

mod common;

use common::*;
use proc_diff::model::{DeltaCategory, FailureCategory};

#[test]
fn sampling_gap_is_reported_but_cumulative_delta_kept() {
    let (engine, expected) = run_fixture("sampling-gap");

    // 跨缺口的累计增量仍然精确（260-150=110），但类别标记为 sampling_gap。
    let d700 = engine.deltas_for(700);
    assert_eq!(d700.len(), 3);
    assert_eq!(d700[1].category, DeltaCategory::Ok);
    assert_eq!(d700[1].delta, Some(50));
    assert_eq!(d700[2].category, DeltaCategory::SamplingGap);
    assert_eq!(d700[2].delta, Some(110));
    assert_eq!((d700[2].from_seq, d700[2].to_seq), (Some(2), 4));

    // 缺口区间进入不可确定报告，delta_known=true 表示累计值仍可信。
    let und = engine.undetermined();
    assert_eq!(und.len(), 2, "pid 1 and pid 700 both span the gap");
    let u700 = und.iter().find(|u| u.identity == id(700, 1)).expect("pid 700 interval");
    assert_eq!(u700.category, FailureCategory::SamplingGap);
    assert!(u700.delta_known);
    assert_eq!((u700.from_seq, u700.to_seq), (2, 4));

    assert_fixture_matches(&engine, &expected);
}
