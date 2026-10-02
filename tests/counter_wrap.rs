//! Counter wraparound vs data anomaly.
//!
//! Fixture (tests/fixtures/counter_wrap), 32-bit counter modulus 2^32,
//! max plausible 200 jiffies/interval:
//! - pid 4000: utime 100 -> 4294967200 -> 50 -> 120. The step 4294967200 -> 50
//!   is a wraparound: 50 + 2^32 - 4294967200 = 146 <= 200, plausible.
//! - pid 4001: utime 1000 -> 1500 -> 400 -> 600. The step 1500 -> 400 is a
//!   regression of 1100 with no plausible wraparound (wrap-adjusted delta
//!   would be ~4.29e9): a data anomaly, interval indeterminate.

mod common;

use common::*;
use procdiff::delta::DeltaClass;
use procdiff::diag::Decision;

#[test]
fn wraparound_yields_wrap_adjusted_delta() {
    let engine = ingest_case("counter_wrap");
    let deltas = deltas_for(&engine, 4000);
    let shape: Vec<(u64, u64, DeltaClass, Option<u64>)> = deltas
        .iter()
        .map(|d| (d.from_seq, d.to_seq, d.class, d.cpu_jiffies))
        .collect();
    assert_eq!(
        shape,
        vec![
            (1, 2, DeltaClass::Ok, Some(4_294_967_100)),
            (2, 3, DeltaClass::Wrap, Some(146)),
            (3, 4, DeltaClass::Ok, Some(70)),
        ]
    );
}

#[test]
fn unexplained_regression_is_anomaly_not_wrap() {
    let engine = ingest_case("counter_wrap");
    let deltas = deltas_for(&engine, 4001);
    let shape: Vec<(u64, u64, DeltaClass, Option<u64>)> = deltas
        .iter()
        .map(|d| (d.from_seq, d.to_seq, d.class, d.cpu_jiffies))
        .collect();
    assert_eq!(
        shape,
        vec![
            (1, 2, DeltaClass::Ok, Some(500)),
            (2, 3, DeltaClass::Anomaly, None),
            // After the anomaly the baseline is re-established at 400, so the
            // next interval is exact again: 600 - 400 = 200.
            (3, 4, DeltaClass::Ok, Some(200)),
        ]
    );
}

#[test]
fn wrap_and_anomaly_are_diagnosed_differently() {
    let engine = ingest_case("counter_wrap");

    let wrap = engine
        .state
        .diags
        .iter()
        .find(|r| r.action == "delta" && r.pid == Some(4000))
        .expect("wrap diagnostic");
    assert_eq!(wrap.decision, Decision::Accepted);
    assert_eq!(wrap.key_state["prev_cpu"], 4_294_967_200u64);
    assert_eq!(wrap.key_state["next_cpu"], 50u64);

    let anomaly = engine
        .state
        .diags
        .iter()
        .find(|r| r.action == "delta" && r.pid == Some(4001))
        .expect("anomaly diagnostic");
    assert_eq!(anomaly.decision, Decision::Indeterminate);
    assert!(anomaly.reason.contains("1500 -> 400"));
}
