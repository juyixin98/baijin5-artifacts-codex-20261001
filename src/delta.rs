//! Resource delta algorithm.
//!
//! Cumulative CPU counters are expected to grow monotonically within one
//! process generation. When a counter moves backwards we distinguish three
//! situations:
//!
//! - **Reset**: the PID now belongs to a *new generation* (different
//!   `start_time` / boot id). The old counters are simply not inherited; the
//!   new generation establishes a fresh baseline. Detected by the engine via
//!   identity change, not by this module.
//! - **Wraparound**: same generation, counter decreased, but the decrease is
//!   exactly explained by the counter wrapping at its modulus, and the
//!   wrap-adjusted delta is plausible for the elapsed time.
//! - **Anomaly**: same generation, counter decreased, and no plausible
//!   wraparound explains it. The interval is indeterminate.

use serde::{Deserialize, Serialize};
use std::fmt;

/// Classification of one sampling interval for one process identity.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeltaClass {
    /// Counters monotonic; delta is exact.
    Ok,
    /// New generation of a reused PID; counters re-baselined, nothing inherited.
    Reset,
    /// Counter wrapped at its modulus; delta computed modulo the modulus.
    Wrap,
    /// Counter regressed without a plausible explanation; interval indeterminate.
    Anomaly,
    /// The process's stat was unreadable in this sample; interval indeterminate.
    PartialRead,
    /// One or more snapshots are missing inside this interval; indeterminate.
    MissingSamples,
}

impl fmt::Display for DeltaClass {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let s = match self {
            DeltaClass::Ok => "ok",
            DeltaClass::Reset => "reset",
            DeltaClass::Wrap => "wrap",
            DeltaClass::Anomaly => "anomaly",
            DeltaClass::PartialRead => "partial_read",
            DeltaClass::MissingSamples => "missing_samples",
        };
        f.write_str(s)
    }
}

/// Result of comparing two cumulative counter readings of one generation.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CounterDelta {
    /// `next >= prev`; exact delta.
    Monotonic(u64),
    /// `next < prev` but explainable by wraparound at the modulus; delta is
    /// the wrap-adjusted value.
    Wrapped(u64),
    /// `next < prev` with no plausible wraparound; data anomaly.
    Anomalous,
}

/// Classify a pair of cumulative counter readings.
///
/// * `prev`, `next` — cumulative counter values (jiffies).
/// * `elapsed_intervals` — number of sampling intervals between the readings
///   (>= 1).
/// * `modulus` — counter modulus (e.g. 2^32).
/// * `max_plausible_per_interval` — upper bound of plausible consumption per
///   interval; used to tell a wraparound apart from corrupt data.
pub fn classify_counter(
    prev: u64,
    next: u64,
    elapsed_intervals: u64,
    modulus: u64,
    max_plausible_per_interval: u64,
) -> CounterDelta {
    if next >= prev {
        return CounterDelta::Monotonic(next - prev);
    }
    // Same-generation regression. If the counter is `modulus`-wide, a
    // wraparound explains the regression as `next + modulus - prev`.
    let wrapped = next.saturating_add(modulus).saturating_sub(prev);
    let bound = max_plausible_per_interval.saturating_mul(elapsed_intervals.max(1));
    if wrapped <= bound {
        CounterDelta::Wrapped(wrapped)
    } else {
        CounterDelta::Anomalous
    }
}

/// One computed (or explicitly indeterminate) sampling interval.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct IntervalDelta {
    pub identity: crate::model::ProcessIdentity,
    pub from_seq: crate::model::Seq,
    pub to_seq: crate::model::Seq,
    /// CPU jiffies consumed in the interval; `None` when indeterminate.
    pub cpu_jiffies: Option<u64>,
    /// RSS in pages at `to_seq`; `None` when not observed.
    pub rss_pages: Option<i64>,
    pub class: DeltaClass,
    /// Human-readable reason, safe to log (no sensitive content).
    pub reason: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    const MOD: u64 = 1u64 << 32;

    #[test]
    fn monotonic_growth_is_exact() {
        assert_eq!(
            classify_counter(100, 150, 1, MOD, 200),
            CounterDelta::Monotonic(50)
        );
    }

    #[test]
    fn equal_counters_are_zero_delta() {
        assert_eq!(classify_counter(42, 42, 1, MOD, 200), CounterDelta::Monotonic(0));
    }

    #[test]
    fn small_regression_near_modulus_is_wrap() {
        // 2^32 - 96 -> 50 : wrapped delta = 50 + 2^32 - (2^32 - 96) = 146
        assert_eq!(
            classify_counter(MOD - 96, 50, 1, MOD, 200),
            CounterDelta::Wrapped(146)
        );
    }

    #[test]
    fn large_regression_is_anomaly() {
        // 1000 -> 10 : wrap-adjusted delta ~= 4.29e9, far above the bound.
        assert_eq!(
            classify_counter(1000, 10, 1, MOD, 200),
            CounterDelta::Anomalous
        );
    }

    #[test]
    fn wrap_bound_scales_with_elapsed_intervals() {
        // 2^32 - 50 -> 400 : wrapped delta = 450, plausible over 3 intervals
        // with bound 200/interval (600) but not over 1 interval.
        assert_eq!(
            classify_counter(MOD - 50, 400, 1, MOD, 200),
            CounterDelta::Anomalous
        );
        assert_eq!(
            classify_counter(MOD - 50, 400, 3, MOD, 200),
            CounterDelta::Wrapped(450)
        );
    }
}
