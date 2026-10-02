//! cfs-sim: single-CPU CFS-style fair scheduling simulator.
//!
//! Simplification scope (NOT a full Linux CFS replica):
//! - one logical CPU, tick-based virtual clock (no wall-clock execution);
//! - flat task list: no cgroups / group scheduling, no SMP load balancing;
//! - weights are given directly (no nice->load table lookup beyond NICE_0_LOAD);
//! - no real-time / deadline classes, no priority inheritance, no autogroup;
//! - blocking is modeled as scripted Sleep phases, not kernel wait queues.
//!
//! Fixed formulas (see config.rs):
//! - vruntime delta:  dv = exec_ms * NICE_0_LOAD * VRUNTIME_SCALE / weight
//! - timeslice:       slice_i = max(min_granularity, target_latency * w_i / sum_w)
//! - wakeup placement: v = max(own_v, min_vruntime - wakeup_bonus_v), bounded
//!   so a waking task cannot preempt others indefinitely.

pub mod config;
pub mod diag;
pub mod metrics;
pub mod persist;
pub mod runqueue;
pub mod scenario;
pub mod scheduler;
pub mod task;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
