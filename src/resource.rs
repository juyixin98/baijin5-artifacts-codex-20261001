//! Resource budgets and memory accounting.
//!
//! The external algorithm must remain correct under tight memory and disk
//! limits. All budgets are explicit configuration (with sane defaults) and are
//! enforced at the point they would be crossed, producing a
//! `resource_exhausted` error with a specific code rather than aborting the
//! process.

use crate::error::{Result, SetOpError};

/// Tunable budgets for one execution.
#[derive(Debug, Clone)]
pub struct Budget {
    /// Resident bytes allowed for the in-memory fast path. Above it the engine
    /// takes the partitioned/spilled path.
    pub memory_bytes: usize,
    /// Bytes that may be buffered in one partition before flushing to disk.
    pub partition_buffer_bytes: usize,
    /// Resident bytes the per-partition hash table may occupy before the
    /// partition is recursively divided.
    pub partition_table_bytes: usize,
    /// Maximum total bytes written to spill files.
    pub spill_bytes: u64,
    /// Maximum number of spill segment files.
    pub spill_files: usize,
    /// Output rows buffered before a flush; bounds result-assembly memory.
    pub output_buffer_rows: usize,
}

impl Default for Budget {
    fn default() -> Self {
        Self {
            memory_bytes: 64 * 1024 * 1024,
            partition_buffer_bytes: 1024 * 1024,
            partition_table_bytes: 8 * 1024 * 1024,
            spill_bytes: 256 * 1024 * 1024,
            spill_files: 4096,
            output_buffer_rows: 4096,
        }
    }
}

impl Budget {
    pub fn validate(&self) -> Result<()> {
        if self.memory_bytes == 0
            || self.partition_buffer_bytes == 0
            || self.partition_table_bytes == 0
            || self.spill_bytes == 0
            || self.spill_files == 0
            || self.output_buffer_rows == 0
        {
            return Err(SetOpError::input(
                "invalid_budget",
                "all budget values must be non-zero",
            ));
        }
        Ok(())
    }
}

/// Conservative resident-size estimate for a key plus its hash-table overhead.
pub fn key_table_cost(key_len: usize) -> usize {
    // key bytes + HashMap node/overhead estimate (two u64 counts, hash,
    // capacity slack). This deliberately over-estimates.
    const PER_ENTRY_OVERHEAD: usize = 72;
    key_len + PER_ENTRY_OVERHEAD
}

/// Add two multiplicities, rejecting 64-bit overflow as resource exhaustion.
///
/// Row counts are cardinalities of multisets; an overflow means the result
/// cannot be represented and the run must fail loudly rather than wrap.
pub fn checked_add_counts(a: u64, b: u64) -> Result<u64> {
    a.checked_add(b)
        .ok_or_else(|| SetOpError::resource("count_overflow", "row multiplicity overflowed u64"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::error::ErrorKind;

    #[test]
    fn overflow_is_resource_exhausted_count_overflow() {
        let err = checked_add_counts(u64::MAX, 1).unwrap_err();
        assert_eq!(err.kind, ErrorKind::ResourceExhausted);
        assert_eq!(err.code, "count_overflow");
        assert_eq!(checked_add_counts(5, 7).unwrap(), 12);
    }

    #[test]
    fn zero_budget_rejected_as_input() {
        let b = Budget {
            spill_files: 0,
            ..Budget::default()
        };
        assert_eq!(b.validate().unwrap_err().code, "invalid_budget");
    }
}
