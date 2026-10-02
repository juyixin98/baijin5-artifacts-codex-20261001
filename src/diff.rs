//! Resource algorithm: pure counter-delta classification.
//!
//! Cumulative CPU counters can go backwards for two very different reasons:
//! the counter wrapped around its maximum (legitimate, delta recoverable) or
//! the data is anomalous (interval undeterminable). Identity resets (PID
//! reuse) are handled by the engine, not here — by the time this function is
//! called both samples are known to belong to the same identity.

/// Result of comparing two cumulative counter values of the same identity.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum CounterClass {
    /// Monotonic advance by the contained amount.
    Advance(u64),
    /// Counter wrapped past `counter_max`; contained value is the delta.
    Wrap(u64),
    /// Backwards jump that cannot be a plausible wrap: data anomaly.
    Anomaly,
}

/// Classify the transition `prev -> next` for a cumulative counter that wraps
/// at `counter_max`. A backwards jump is accepted as a wrap only when the
/// implied wrapped delta does not exceed `wrap_max_plausible_delta`.
pub fn classify_counter(
    prev: u64,
    next: u64,
    counter_max: u64,
    wrap_max_plausible_delta: u64,
) -> CounterClass {
    if next >= prev {
        return CounterClass::Advance(next - prev);
    }
    let wrapped = (counter_max - prev) + next + 1;
    if wrapped <= wrap_max_plausible_delta {
        CounterClass::Wrap(wrapped)
    } else {
        CounterClass::Anomaly
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn advance_is_plain_difference() {
        assert_eq!(classify_counter(100, 160, u64::MAX, 1000), CounterClass::Advance(60));
    }

    #[test]
    fn small_backwards_jump_within_wrap_budget_is_wrap() {
        // max=1000, 950 -> 30 wraps to 1000-950+30+1 = 81
        assert_eq!(classify_counter(950, 30, 1000, 200), CounterClass::Wrap(81));
    }

    #[test]
    fn large_backwards_jump_is_anomaly() {
        // max=1000, 800 -> 100 would imply 301 > budget 200
        assert_eq!(classify_counter(800, 100, 1000, 200), CounterClass::Anomaly);
    }
}
