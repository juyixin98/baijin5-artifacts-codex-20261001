//! Scheduler tunables and the fixed fairness formulas.
//!
//! Relationship between minimum granularity and target latency:
//! while `nr_running <= target_latency_ms / min_granularity_ms` every task is
//! guaranteed a slice of `target_latency * w_i / sum_w` (>= min_granularity)
//! inside one latency period. Beyond that threshold the latency target can no
//! longer be met and the period stretches to `nr_running * min_granularity`.

use serde::{Deserialize, Serialize};
use thiserror::Error;

/// Load of a task at nice 0 (Linux NICE_0_LOAD analogue).
pub const NICE_0_LOAD: u64 = 1024;

/// Fixed-point scale for vruntime so fractional deltas do not round away.
/// One millisecond of execution at weight == NICE_0_LOAD advances vruntime by
/// exactly `VRUNTIME_SCALE` units.
pub const VRUNTIME_SCALE: u64 = 1 << 20;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct SchedulerConfig {
    /// Length of one simulation tick in milliseconds.
    pub tick_ms: u64,
    /// Target latency: every runnable task should run at least once per period.
    pub target_latency_ms: u64,
    /// Minimum timeslice; floor for the per-task slice.
    pub min_granularity_ms: u64,
    /// Wakeup bonus: how far behind min_vruntime a woken task may be placed.
    pub wakeup_granularity_ms: u64,
}

impl Default for SchedulerConfig {
    fn default() -> Self {
        Self {
            tick_ms: 1,
            target_latency_ms: 8,
            min_granularity_ms: 2,
            wakeup_granularity_ms: 2,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum ConfigError {
    #[error("tick_ms must be >= 1")]
    TickZero,
    #[error("min_granularity_ms ({min}) must be >= tick_ms ({tick})")]
    GranularityBelowTick { min: u64, tick: u64 },
    #[error("target_latency_ms ({latency}) must be >= min_granularity_ms ({min})")]
    LatencyBelowGranularity { latency: u64, min: u64 },
    #[error("wakeup_granularity_ms ({wake}) must be <= target_latency_ms ({latency})")]
    WakeupGranularityTooLarge { wake: u64, latency: u64 },
}

impl SchedulerConfig {
    pub fn validate(&self) -> Result<(), ConfigError> {
        if self.tick_ms == 0 {
            return Err(ConfigError::TickZero);
        }
        if self.min_granularity_ms < self.tick_ms {
            return Err(ConfigError::GranularityBelowTick {
                min: self.min_granularity_ms,
                tick: self.tick_ms,
            });
        }
        if self.target_latency_ms < self.min_granularity_ms {
            return Err(ConfigError::LatencyBelowGranularity {
                latency: self.target_latency_ms,
                min: self.min_granularity_ms,
            });
        }
        if self.wakeup_granularity_ms > self.target_latency_ms {
            return Err(ConfigError::WakeupGranularityTooLarge {
                wake: self.wakeup_granularity_ms,
                latency: self.target_latency_ms,
            });
        }
        Ok(())
    }

    /// Timeslice for a task: max(min_granularity, target_latency * w / sum_w).
    pub fn slice_ms(&self, weight: u64, total_weight: u64) -> u64 {
        let ideal = self.target_latency_ms.saturating_mul(weight) / total_weight.max(1);
        ideal.max(self.min_granularity_ms)
    }

    /// Upper bound on the scheduling period with `nr_running` runnable tasks.
    /// Equals target_latency while nr_running <= latency/min_granularity,
    /// otherwise stretches to nr_running * min_granularity.
    pub fn period_bound_ms(&self, nr_running: u64) -> u64 {
        self.target_latency_ms
            .max(nr_running.saturating_mul(self.min_granularity_ms))
    }

    /// Wakeup bonus expressed in vruntime units for a task of `weight`.
    pub fn wakeup_bonus_vruntime(&self, weight: u64) -> u64 {
        delta_vruntime(self.wakeup_granularity_ms, weight)
    }
}

/// Fixed formula: dv = exec_ms * NICE_0_LOAD * VRUNTIME_SCALE / weight.
pub fn delta_vruntime(exec_ms: u64, weight: u64) -> u64 {
    exec_ms.saturating_mul(NICE_0_LOAD).saturating_mul(VRUNTIME_SCALE) / weight.max(1)
}
