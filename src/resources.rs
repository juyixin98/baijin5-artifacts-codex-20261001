//! Resource accounting: memory budget, spill files, tracked tasks.
//!
//! One [`ResourceRegistry`] exists per query execution. Operators acquire
//! [`Reservation`] guards for internal buffers and [`SpillFile`] guards for
//! temporary files; both release automatically on drop, so teardown is
//! correct even when `close()` is skipped (e.g. downstream early-stop that
//! simply drops the tree). `close()` still exists to make reclamation
//! *deterministic and observable* — tests assert the snapshot returns to zero.
//!
//! All counters are atomic; the registry is shared as `Arc` across operators.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use serde::Serialize;

use crate::error::QueryError;

#[derive(Debug, Clone, Copy, Serialize)]
pub struct ResourceSnapshot {
    pub memory_used_bytes: usize,
    pub memory_limit_bytes: usize,
    pub reservations_live: usize,
    pub spill_files_live: usize,
    pub spill_files_created_total: usize,
    pub spill_bytes_written_total: usize,
    pub tasks_live: usize,
}

pub struct ResourceRegistry {
    memory_limit: usize,
    memory_used: AtomicUsize,
    reservations_live: AtomicUsize,
    spill_dir: PathBuf,
    spill_seq: AtomicUsize,
    spill_files_live: AtomicUsize,
    spill_files_created: AtomicUsize,
    spill_bytes_written: AtomicUsize,
    tasks_live: AtomicUsize,
}

impl ResourceRegistry {
    pub fn new(memory_limit: usize, spill_dir: PathBuf) -> Arc<Self> {
        std::fs::create_dir_all(&spill_dir).expect("create spill dir");
        Arc::new(Self {
            memory_limit,
            memory_used: AtomicUsize::new(0),
            reservations_live: AtomicUsize::new(0),
            spill_dir,
            spill_seq: AtomicUsize::new(0),
            spill_files_live: AtomicUsize::new(0),
            spill_files_created: AtomicUsize::new(0),
            spill_bytes_written: AtomicUsize::new(0),
            tasks_live: AtomicUsize::new(0),
        })
    }

    /// Reserve `bytes` against the memory budget. Returns a guard that
    /// releases on drop. Failure is a `ResourceExhausted` error, never a
    /// silent overcommit.
    pub fn reserve(self: &Arc<Self>, bytes: usize) -> Result<Reservation, QueryError> {
        let mut current = self.memory_used.load(Ordering::Acquire);
        loop {
            let new = current.saturating_add(bytes);
            if new > self.memory_limit {
                return Err(QueryError::resource(format!(
                    "memory budget exceeded: {current} used + {bytes} requested > {} limit",
                    self.memory_limit
                )));
            }
            match self.memory_used.compare_exchange_weak(
                current,
                new,
                Ordering::AcqRel,
                Ordering::Acquire,
            ) {
                Ok(_) => {
                    self.reservations_live.fetch_add(1, Ordering::AcqRel);
                    return Ok(Reservation { registry: Arc::clone(self), bytes });
                }
                Err(actual) => current = actual,
            }
        }
    }

    /// Create a new spill file guard. The file is deleted when the guard
    /// drops; `spill_files_live` tracks guards currently alive.
    pub fn create_spill_file(self: &Arc<Self>) -> Result<SpillFile, QueryError> {
        let seq = self.spill_seq.fetch_add(1, Ordering::AcqRel);
        let path = self.spill_dir.join(format!("spill-{seq:06}.ipc"));
        // Create eagerly so a missing-directory error surfaces here.
        std::fs::File::create(&path)?;
        self.spill_files_live.fetch_add(1, Ordering::AcqRel);
        self.spill_files_created.fetch_add(1, Ordering::AcqRel);
        Ok(SpillFile { path, registry: Arc::clone(self) })
    }

    pub fn record_spill_write(&self, bytes: usize) {
        self.spill_bytes_written.fetch_add(bytes, Ordering::AcqRel);
    }

    /// Spawn a task whose lifetime is tracked. `tasks_live` returns to zero
    /// only when every tracked task has finished (or been aborted), which is
    /// what leak assertions check.
    pub fn spawn_tracked<F>(self: &Arc<Self>, fut: F) -> tokio::task::JoinHandle<F::Output>
    where
        F: std::future::Future + Send + 'static,
        F::Output: Send + 'static,
    {
        self.tasks_live.fetch_add(1, Ordering::AcqRel);
        let registry = Arc::clone(self);
        tokio::spawn(async move {
            let _guard = TaskGuard(registry);
            fut.await
        })
    }

    pub fn snapshot(&self) -> ResourceSnapshot {
        ResourceSnapshot {
            memory_used_bytes: self.memory_used.load(Ordering::Acquire),
            memory_limit_bytes: self.memory_limit,
            reservations_live: self.reservations_live.load(Ordering::Acquire),
            spill_files_live: self.spill_files_live.load(Ordering::Acquire),
            spill_files_created_total: self.spill_files_created.load(Ordering::Acquire),
            spill_bytes_written_total: self.spill_bytes_written.load(Ordering::Acquire),
            tasks_live: self.tasks_live.load(Ordering::Acquire),
        }
    }

    pub fn spill_dir(&self) -> &Path {
        &self.spill_dir
    }
}

/// Memory budget guard. Dropping releases the bytes exactly once.
pub struct Reservation {
    registry: Arc<ResourceRegistry>,
    bytes: usize,
}

impl Reservation {
    pub fn bytes(&self) -> usize {
        self.bytes
    }
}

impl Drop for Reservation {
    fn drop(&mut self) {
        self.registry.memory_used.fetch_sub(self.bytes, Ordering::AcqRel);
        self.registry.reservations_live.fetch_sub(1, Ordering::AcqRel);
    }
}

/// Guard for one spill file on disk. Deleting on drop guarantees no orphan
/// files even when an operator errors out mid-merge.
pub struct SpillFile {
    path: PathBuf,
    registry: Arc<ResourceRegistry>,
}

impl SpillFile {
    pub fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for SpillFile {
    fn drop(&mut self) {
        if self.path.exists() {
            let _ = std::fs::remove_file(&self.path);
        }
        self.registry.spill_files_live.fetch_sub(1, Ordering::AcqRel);
    }
}

struct TaskGuard(Arc<ResourceRegistry>);

impl Drop for TaskGuard {
    fn drop(&mut self) {
        self.0.tasks_live.fetch_sub(1, Ordering::AcqRel);
    }
}
