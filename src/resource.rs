//! Resource limits and accounting.
//!
//! Limits are explicit and fail closed: exceeding a budget produces a typed
//! [`ResourceCode`] error, never a silent `abort`/OOM. Tests drive execution
//! with tiny budgets to prove external-memory paths run for real.

use serde::{Deserialize, Serialize};

use crate::error::{ResourceCode, SetOpsError};

/// Per-query resource contract.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct ResourceLimits {
    /// Maximum bytes of resident hash-table data (canonical row keys, not
    /// including fixed allocator overhead) held at one time per side.
    /// When exceeded the operator spills partitions to disk.
    pub memory_bytes: usize,
    /// Disable spilling entirely; exceeding `memory_bytes` then fails with
    /// `resource_exhausted/memory_budget`. Used by tests that assert the
    /// guard exists.
    pub allow_spill: bool,
    /// Number of partitions at each recursion level.
    pub partition_fanout: usize,
    /// Maximum recursive partition depth. Beyond this, a partition whose
    /// distinct keys still do not fit fails with `partition_depth`.
    pub max_partition_depth: usize,
    /// Multiplicity cap for a single distinct row. Any addition that would
    /// exceed it fails with `count_overflow` (the overflow-rejection rule).
    /// Defaults to u64::MAX; tests set it small.
    pub max_count: u64,
    /// Cap on total output rows (materialised or streamed accounting).
    pub max_output_rows: u64,
    /// Cap on bytes written into one spill directory.
    pub spill_bytes: u64,
}

impl Default for ResourceLimits {
    fn default() -> Self {
        Self {
            memory_bytes: 64 * 1024 * 1024,
            allow_spill: true,
            partition_fanout: 64,
            max_partition_depth: 8,
            max_count: u64::MAX,
            spill_bytes: 1 << 40,
            max_output_rows: u64::MAX,
        }
    }
}

impl ResourceLimits {
    /// Tiny-budget profile used by tests and examples: every non-trivial
    /// dataset must spill.
    pub fn tight(memory_bytes: usize) -> Self {
        Self {
            memory_bytes,
            allow_spill: true,
            partition_fanout: 4,
            max_partition_depth: 6,
            ..Self::default()
        }
    }

    pub fn no_spill(memory_bytes: usize) -> Self {
        Self {
            allow_spill: false,
            ..Self::tight(memory_bytes)
        }
    }

    pub fn validate(&self) -> Result<(), SetOpsError> {
        if self.partition_fanout < 2 {
            return Err(SetOpsError::input(
                crate::error::InputCode::InvalidRequest,
                "partition_fanout must be >= 2",
            ));
        }
        if self.memory_bytes == 0 {
            return Err(SetOpsError::input(
                crate::error::InputCode::InvalidRequest,
                "memory_bytes must be > 0",
            ));
        }
        Ok(())
    }

    pub(crate) fn count_overflow_err(&self) -> SetOpsError {
        SetOpsError::resource(
            ResourceCode::CountOverflow,
            format!(
                "row multiplicity exceeds configured cap of {}",
                self.max_count
            ),
        )
    }
}
