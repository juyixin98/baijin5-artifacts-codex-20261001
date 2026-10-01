//! In-memory state budget accounting.
//!
//! A [`Budget`] is created per query execution and tracks *retained state*
//! (rows held in the top-N structure, including the WITH TIES shelf). Both
//! row count and an approximate byte cost are accounted, and the **peak** is
//! remembered for the execution stats — tests assert on it.
//!
//! Budgets are explicit: a limit of zero means "unlimited", never "zero
//! bytes".

use serde::{Deserialize, Serialize};
use sl_types::{ErrorCategory, SlError};

/// Fixed per-entry overhead added on top of the scalar payload bytes. It
/// covers the heap node, `Vec` capacities, identity and bookkeeping pointers.
/// Kept as a named constant so the accounting basis is auditable.
pub const PER_ENTRY_OVERHEAD_BYTES: usize = 64;

/// Snapshot of budget usage (also returned in execution stats).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct BudgetSnapshot {
    pub rows: usize,
    pub estimated_bytes: usize,
    pub peak_rows: usize,
    pub peak_bytes: usize,
    pub row_limit: usize,
    pub byte_limit: usize,
}

/// Tracks retained-state usage for one query.
#[derive(Debug, Clone)]
pub struct Budget {
    row_limit: usize,
    byte_limit: usize,
    rows: usize,
    bytes: usize,
    peak_rows: usize,
    peak_bytes: usize,
}

impl Budget {
    /// `row_limit == 0` / `byte_limit == 0` mean unlimited on that axis.
    pub fn new(row_limit: usize, byte_limit: usize) -> Self {
        Self {
            row_limit,
            byte_limit,
            rows: 0,
            bytes: 0,
            peak_rows: 0,
            peak_bytes: 0,
        }
    }

    pub fn rows(&self) -> usize {
        self.rows
    }

    pub fn estimated_bytes(&self) -> usize {
        self.bytes
    }

    pub fn peak_rows(&self) -> usize {
        self.peak_rows
    }

    pub fn peak_bytes(&self) -> usize {
        self.peak_bytes
    }

    /// Estimate cost for retaining `n` rows whose payloads sum to
    /// `payload_bytes`.
    pub fn entry_cost(n: usize, payload_bytes: usize) -> usize {
        payload_bytes.saturating_add(n.saturating_mul(PER_ENTRY_OVERHEAD_BYTES))
    }

    /// Would adding `delta_rows` / `delta_payload_bytes` cross a configured
    /// limit? Pure check, does not mutate state.
    pub fn would_exceed(&self, delta_rows: usize, delta_payload_bytes: usize) -> bool {
        let new_rows = self.rows.saturating_add(delta_rows);
        let new_bytes = self
            .bytes
            .saturating_add(Self::entry_cost(delta_rows, delta_payload_bytes));
        let rows_exceeded = self.row_limit != 0 && new_rows > self.row_limit;
        let bytes_exceeded = self.byte_limit != 0 && new_bytes > self.byte_limit;
        rows_exceeded || bytes_exceeded
    }

    /// Record that state grew. Returns a categorized [`SlError`] in the
    /// `budget_exceeded` category when a limit is crossed; the caller decides
    /// whether to surface that error (reject policy) or to spill instead.
    pub fn reserve(
        &mut self,
        delta_rows: usize,
        delta_payload_bytes: usize,
    ) -> Result<(), SlError> {
        if self.would_exceed(delta_rows, delta_payload_bytes) {
            return Err(SlError::new(
                ErrorCategory::BudgetExceeded,
                "state_budget_exceeded",
                format!(
                    "retained state would grow to {} rows / ~{} bytes; budget is {} rows / {} bytes",
                    self.rows.saturating_add(delta_rows),
                    self.bytes
                        .saturating_add(Self::entry_cost(delta_rows, delta_payload_bytes)),
                    self.row_limit,
                    self.byte_limit
                ),
                "sl_resource::budget",
            ));
        }
        self.commit(delta_rows, delta_payload_bytes);
        Ok(())
    }

    /// Record growth unconditionally (used by the external-select path, which
    /// answers overflow by spilling rather than refusing).
    pub fn commit(&mut self, delta_rows: usize, delta_payload_bytes: usize) {
        self.rows = self.rows.saturating_add(delta_rows);
        self.bytes = self
            .bytes
            .saturating_add(Self::entry_cost(delta_rows, delta_payload_bytes));
        self.peak_rows = self.peak_rows.max(self.rows);
        self.peak_bytes = self.peak_bytes.max(self.bytes);
    }

    /// Release retained state (eviction or spill of `delta_rows`).
    pub fn release(&mut self, delta_rows: usize, freed_payload_bytes: usize) {
        let freed = Self::entry_cost(delta_rows, freed_payload_bytes);
        self.rows = self.rows.saturating_sub(delta_rows);
        self.bytes = self.bytes.saturating_sub(freed);
    }

    pub fn snapshot(&self) -> BudgetSnapshot {
        BudgetSnapshot {
            rows: self.rows,
            estimated_bytes: self.bytes,
            peak_rows: self.peak_rows,
            peak_bytes: self.peak_bytes,
            row_limit: self.row_limit,
            byte_limit: self.byte_limit,
        }
    }
}
