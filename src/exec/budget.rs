//! Memory budget accounting, including the string byte buffer.
//!
//! All large live allocations in the execution engine reserve bytes from one
//! shared [`MemoryBudget`] *before* growing.  When the reservation would exceed
//! the limit, the ingest operator spills its sorted buffer to disk instead of
//! growing further — so a skewed group or long strings cannot produce
//! unbounded in-memory accumulation.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use crate::error::{Error, ErrorKind, Result};

/// Fixed accounting overhead per [`crate::exec::cells::Cell`] / record so the
/// budget reflects container capacity, not only payload bytes.
pub const RECORD_OVERHEAD_BYTES: usize = 64;

#[derive(Debug)]
struct Inner {
    limit: usize,
    used: AtomicUsize,
    peak: AtomicUsize,
}

/// Shared, clonable memory budget.
#[derive(Debug, Clone)]
pub struct MemoryBudget {
    inner: Arc<Inner>,
}

impl MemoryBudget {
    pub fn new(limit_bytes: usize) -> Self {
        Self {
            inner: Arc::new(Inner {
                limit: limit_bytes,
                used: AtomicUsize::new(0),
                peak: AtomicUsize::new(0),
            }),
        }
    }

    pub fn limit(&self) -> usize {
        self.inner.limit
    }

    pub fn used(&self) -> usize {
        self.inner.used.load(Ordering::Relaxed)
    }

    pub fn peak(&self) -> usize {
        self.inner.peak.load(Ordering::Relaxed)
    }

    /// Reserve `bytes`, returning [`ErrorKind::BudgetExceeded`] instead of
    /// overshooting the limit.  The high-water mark only reflects accepted
    /// reservations, so it never exceeds the configured limit.
    pub fn try_grow(&self, bytes: usize) -> Result<()> {
        let prev = self.inner.used.load(Ordering::Relaxed);
        let now = prev.saturating_add(bytes);
        if now > self.inner.limit {
            return Err(Error::new(
                ErrorKind::BudgetExceeded,
                format!(
                    "memory budget of {} bytes exceeded: reservation of {bytes} bytes rejected at {prev} bytes used",
                    self.inner.limit
                ),
            ));
        }
        self.inner.used.store(now, Ordering::Relaxed);
        self.inner.peak.fetch_max(now, Ordering::Relaxed);
        Ok(())
    }

    /// Release previously reserved bytes.
    pub fn shrink(&self, bytes: usize) {
        self.inner.used.fetch_sub(bytes, Ordering::Relaxed);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_oversize_reservation_and_tracks_peak() {
        let budget = MemoryBudget::new(100);
        budget.try_grow(60).unwrap();
        assert_eq!(budget.used(), 60);
        assert_eq!(
            budget.try_grow(50).unwrap_err().kind,
            ErrorKind::BudgetExceeded
        );
        // Rejected reservation never took effect.
        assert_eq!(budget.used(), 60);
        budget.try_grow(40).unwrap();
        assert_eq!(budget.peak(), 100);
        budget.shrink(40);
        assert_eq!(budget.used(), 60);
    }
}
