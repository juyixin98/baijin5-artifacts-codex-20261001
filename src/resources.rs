//! Resource accounting and execution state primitives.
//!
//! - [`MemoryGuard`] charges the resident budget *including* string payload
//!   bytes, so a run of huge strings cannot silently exhaust memory.
//! - [`SpillManager`] owns the spill directory and enforces a disk quota.
//! - [`CancellationToken`] supports cooperative cancellation at run
//!   boundaries; already-spilled runs are durable and resumable.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::Arc;

use crate::diagnostics::RequestId;
use crate::error::{ErrorKind, PctlError, Result};

/// Shared, clonable resident-memory budget for one query.
#[derive(Clone)]
pub struct MemoryGuard {
    inner: Arc<MemoryInner>,
}

struct MemoryInner {
    used: AtomicUsize,
    high_water: AtomicUsize,
    limit: usize,
}

impl MemoryGuard {
    pub fn new(limit: usize) -> Self {
        Self {
            inner: Arc::new(MemoryInner {
                used: AtomicUsize::new(0),
                high_water: AtomicUsize::new(0),
                limit,
            }),
        }
    }

    pub fn limit(&self) -> usize {
        self.inner.limit
    }

    pub fn used(&self) -> usize {
        self.inner.used.load(Ordering::Acquire)
    }

    pub fn high_water(&self) -> usize {
        self.inner.high_water.load(Ordering::Acquire)
    }

    pub fn remaining(&self) -> usize {
        self.inner.limit.saturating_sub(self.used())
    }

    /// Charge `bytes` or fail without partially reserving.
    pub fn charge(&self, bytes: usize) -> Result<()> {
        let used = self.inner.used.fetch_add(bytes, Ordering::AcqRel) + bytes;
        if used > self.inner.limit {
            // roll back so a rejected charge does not poison the account
            self.inner.used.fetch_sub(bytes, Ordering::AcqRel);
            Err(PctlError::new(
                ErrorKind::Resource,
                "memory_budget_exceeded",
                format!(
                    "resident budget exceeded: need {bytes} more, {} of {} used",
                    used.saturating_sub(bytes),
                    self.inner.limit
                ),
            ))
        } else {
            self.inner
                .high_water
                .fetch_update(Ordering::AcqRel, Ordering::Acquire, |hw| Some(hw.max(used)))
                .ok();
            Ok(())
        }
    }

    pub fn release(&self, bytes: usize) {
        let prev = self.inner.used.fetch_sub(bytes, Ordering::AcqRel);
        debug_assert!(prev >= bytes, "released more memory than charged");
    }
}

/// Shared, clonable cancellation flag.
#[derive(Clone, Default)]
pub struct CancellationToken {
    cancelled: Arc<AtomicBool>,
}

impl CancellationToken {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn cancel(&self) {
        self.cancelled.store(true, Ordering::Release);
    }

    pub fn is_cancelled(&self) -> bool {
        self.cancelled.load(Ordering::Acquire)
    }

    pub fn check(&self) -> Result<()> {
        if self.is_cancelled() {
            Err(PctlError::new(
                ErrorKind::Cancelled,
                "cancelled",
                "operator execution cancelled at a safe boundary; spilled runs are preserved",
            ))
        } else {
            Ok(())
        }
    }
}

/// Owns spill files and the global disk quota for one query.
#[derive(Clone)]
pub struct SpillManager {
    root: PathBuf,
    total_bytes: Arc<AtomicU64>,
    max_bytes: u64,
}

impl SpillManager {
    pub fn new(base_dir: &Path, rid: &RequestId, max_bytes: u64) -> Result<Self> {
        let root = base_dir.join(format!("query-{rid}"));
        std::fs::create_dir_all(&root)?;
        Ok(Self {
            root,
            total_bytes: Arc::new(AtomicU64::new(0)),
            max_bytes,
        })
    }

    /// Reopen an existing query spill directory on resume, accounting every
    /// artifact already on disk against the quota.
    pub fn reopen(root: PathBuf, max_bytes: u64) -> Result<Self> {
        if !root.is_dir() {
            return Err(PctlError::new(
                ErrorKind::Validation,
                "resume_dir_missing",
                format!("resume spill directory does not exist: {}", root.display()),
            ));
        }
        let mut total = 0u64;
        for entry in std::fs::read_dir(&root)? {
            total += entry?.metadata()?.len();
        }
        Ok(Self {
            root,
            total_bytes: Arc::new(AtomicU64::new(total)),
            max_bytes,
        })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn dir(&self) -> &Path {
        &self.root
    }

    pub fn total_spilled_bytes(&self) -> u64 {
        self.total_bytes.load(Ordering::Acquire)
    }

    pub fn run_path(&self, run_id: u32) -> PathBuf {
        self.root.join(format!("run-{run_id:06}.sst"))
    }

    /// Account a finished run file against the disk quota.
    pub fn account_finished(&self, path: &Path) -> Result<u64> {
        let size = std::fs::metadata(path)?.len();
        let total = self.total_bytes.fetch_add(size, Ordering::AcqRel) + size;
        if total > self.max_bytes {
            // reject and remove the overflowing run; caller treats query as failed
            let _ = std::fs::remove_file(path);
            self.total_bytes.fetch_sub(size, Ordering::AcqRel);
            return Err(PctlError::new(
                ErrorKind::Resource,
                "spill_quota_exceeded",
                format!(
                    "spill quota of {} bytes exceeded by run of {size} bytes",
                    self.max_bytes
                ),
            ));
        }
        Ok(size)
    }

    /// Delete every spill artifact of this query.
    pub fn cleanup(&self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}
