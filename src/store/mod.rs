//! Backing-store layer: the persistent image behind the page cache.
//!
//! The model never touches the filesystem directly; it goes through
//! [`BackingStore`]. Three implementations are provided:
//!
//! - [`MemStore`] — volatile in-memory store, the reusable test fixture.
//! - [`FsStore`] — real files under a root directory (server default).
//! - [`FaultyStore`] — decorator that injects write/flush failures, used to
//!   verify that failed syncs do not clear dirty marks.

mod fault;
mod fs;
mod mem;

pub use fault::FaultyStore;
pub use fs::FsStore;
pub use mem::MemStore;

use std::fmt;

/// Errors a backing store can return. `Injected` is produced by
/// [`FaultyStore`] to simulate IO failure.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StoreError {
    NotFound(String),
    InvalidPath(String),
    Io(String),
    Injected(String),
}

impl fmt::Display for StoreError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            StoreError::NotFound(p) => write!(f, "file not found: {p}"),
            StoreError::InvalidPath(p) => write!(f, "invalid path: {p}"),
            StoreError::Io(m) => write!(f, "io error: {m}"),
            StoreError::Injected(m) => write!(f, "injected fault: {m}"),
        }
    }
}

impl std::error::Error for StoreError {}

/// Persistent byte image behind the model's page cache.
///
/// Reads at/past EOF return a short (possibly empty) buffer — a sparse file
/// legitimately reads as zeros, which is *not* the same as the model
/// zero-filling an illegal access: the model raises `AccessOutOfRange` for
/// pages wholly beyond EOF before ever asking the store.
pub trait BackingStore: Send + Sync {
    /// Create a new zero-filled file of `size` bytes. Fails if it exists.
    fn create(&self, path: &str, size: u64) -> Result<(), StoreError>;
    fn exists(&self, path: &str) -> bool;
    fn size(&self, path: &str) -> Result<u64, StoreError>;
    /// Read up to `len` bytes at `offset`; short result at EOF.
    fn read_at(&self, path: &str, offset: u64, len: usize) -> Result<Vec<u8>, StoreError>;
    /// Write all of `data` at `offset`, extending the file (sparse) if needed.
    fn write_at(&self, path: &str, offset: u64, data: &[u8]) -> Result<(), StoreError>;
    fn truncate(&self, path: &str, size: u64) -> Result<(), StoreError>;
    /// Durability barrier (fsync analogue).
    fn flush(&self, path: &str) -> Result<(), StoreError>;
}
