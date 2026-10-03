//! Explicit write-back adapter. Dirty pages leave the cache ONLY through
//! this trait (eviction, resize, snapshot flush, zero-capacity
//! write-through). Tests inject scripted failures through the same trait.

use crate::arc::PageId;
use crate::store::{FilePageStore, MemStore};

pub trait Writeback: Send + Sync {
    /// Persist a dirty page. Returning `Err` aborts the triggering access;
    /// the cache is left untouched.
    fn writeback(&self, page: PageId, data: &[u8]) -> Result<(), String>;
}

/// Writes dirty pages back into the filesystem page store.
#[derive(Debug, Clone)]
pub struct FsWriteback {
    store: FilePageStore,
}

impl FsWriteback {
    pub fn new(store: FilePageStore) -> Self {
        FsWriteback { store }
    }
}

impl Writeback for FsWriteback {
    fn writeback(&self, page: PageId, data: &[u8]) -> Result<(), String> {
        self.store.store(page, data)
    }
}

/// Writes dirty pages back into an in-memory store (test fixture).
pub struct MemWriteback {
    store: MemStore,
}

impl MemWriteback {
    pub fn new(store: MemStore) -> Self {
        MemWriteback { store }
    }
}

impl Writeback for MemWriteback {
    fn writeback(&self, page: PageId, data: &[u8]) -> Result<(), String> {
        self.store.put(page, data.to_vec());
        Ok(())
    }
}

/// Always succeeds, discards the data. For pure algorithm tests.
pub struct NoFailWriteback;

impl Writeback for NoFailWriteback {
    fn writeback(&self, _page: PageId, _data: &[u8]) -> Result<(), String> {
        Ok(())
    }
}
