//! Physical page store: the resource algorithm of the service.
//!
//! Owns the set of live physical pages, their reference counts, and the
//! page files on disk. It knows nothing about snapshots; callers
//! (the engine) tell it when to allocate, share (`add_ref`), release,
//! or overwrite a page.
//!
//! Contracts:
//! - `alloc_with` fails with `Resource` when `used == capacity`; no file
//!   is created in that case.
//! - `add_ref` / `release` on an unknown or zero-count page fail with
//!   `State` (refcount anomaly) and change nothing.
//! - All page content is written as full pages of exactly `page_size`
//!   bytes; partial-page logic lives in the engine.

use std::{
    collections::BTreeMap,
    fs,
    path::PathBuf,
};

use serde::{Deserialize, Serialize};

use crate::error::AppError;

pub type PhysId = u64;

/// Cumulative service counters, persisted in the manifest so diagnostics
/// survive restarts.
#[derive(Debug, Default, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub struct Counters {
    /// Pages physically copied because they were shared at write time.
    pub cow_copies: u64,
    /// Writes applied in place to exclusively-owned pages.
    pub in_place_writes: u64,
    /// Fresh zero pages allocated for never-written logical pages.
    pub fresh_allocs: u64,
    pub pages_allocated: u64,
    pub pages_freed: u64,
    pub batches_applied: u64,
    pub batches_rejected: u64,
}

pub struct PageStore {
    dir: PathBuf,
    page_size: usize,
    capacity: usize,
    /// Live pages and their reference counts. Invariant: every value >= 1.
    refcounts: BTreeMap<PhysId, u32>,
    next_id: PhysId,
    counters: Counters,
}

impl PageStore {
    pub fn create(dir: PathBuf, page_size: usize, capacity: usize) -> Result<Self, AppError> {
        fs::create_dir_all(&dir)?;
        Ok(PageStore {
            dir,
            page_size,
            capacity,
            refcounts: BTreeMap::new(),
            next_id: 1,
            counters: Counters::default(),
        })
    }

    pub fn restore(
        dir: PathBuf,
        page_size: usize,
        capacity: usize,
        refcounts: BTreeMap<PhysId, u32>,
        next_id: PhysId,
        counters: Counters,
    ) -> Self {
        PageStore { dir, page_size, capacity, refcounts, next_id, counters }
    }

    fn page_path(&self, id: PhysId) -> PathBuf {
        self.dir.join(format!("{id}.page"))
    }

    pub fn page_size(&self) -> usize {
        self.page_size
    }

    pub fn capacity(&self) -> usize {
        self.capacity
    }

    pub fn used(&self) -> usize {
        self.refcounts.len()
    }

    pub fn counters(&self) -> Counters {
        self.counters
    }

    pub fn set_counters(&mut self, counters: Counters) {
        self.counters = counters;
    }

    pub fn counters_mut(&mut self) -> &mut Counters {
        &mut self.counters
    }

    pub fn refcount(&self, id: PhysId) -> Option<u32> {
        self.refcounts.get(&id).copied()
    }

    pub fn refcounts(&self) -> &BTreeMap<PhysId, u32> {
        &self.refcounts
    }

    pub fn next_id(&self) -> PhysId {
        self.next_id
    }

    /// Allocate a fresh physical page with the given full-page content.
    pub fn alloc_with(&mut self, content: &[u8]) -> Result<PhysId, AppError> {
        if content.len() != self.page_size {
            return Err(AppError::Input(format!(
                "page content must be exactly {} bytes, got {}",
                self.page_size,
                content.len()
            )));
        }
        if self.used() >= self.capacity {
            return Err(AppError::Resource(format!(
                "physical page capacity exhausted: {} of {} pages in use",
                self.used(),
                self.capacity
            )));
        }
        let id = self.next_id;
        self.next_id += 1;
        fs::write(self.page_path(id), content)?;
        self.refcounts.insert(id, 1);
        self.counters.pages_allocated += 1;
        Ok(id)
    }

    /// Increment the refcount of a live page (snapshot fork).
    pub fn add_ref(&mut self, id: PhysId) -> Result<(), AppError> {
        match self.refcounts.get_mut(&id) {
            Some(rc) => {
                *rc = rc.checked_add(1).ok_or_else(|| {
                    AppError::State(format!("refcount anomaly: page {id} refcount overflow"))
                })?;
                Ok(())
            }
            None => Err(AppError::State(format!(
                "refcount anomaly: add_ref on unknown page {id}"
            ))),
        }
    }

    /// Decrement the refcount; frees the page (and deletes its file) at zero.
    pub fn release(&mut self, id: PhysId) -> Result<(), AppError> {
        let rc = self.refcounts.get(&id).copied().ok_or_else(|| {
            AppError::State(format!("refcount anomaly: release on unknown page {id}"))
        })?;
        if rc == 0 {
            return Err(AppError::State(format!(
                "refcount anomaly: release would underflow page {id}"
            )));
        }
        if rc == 1 {
            self.refcounts.remove(&id);
            let path = self.page_path(id);
            match fs::remove_file(&path) {
                Ok(()) => {}
                // A missing file at free time is logged via counters but not
                // fatal: the page is gone either way.
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
                Err(e) => return Err(e.into()),
            }
            self.counters.pages_freed += 1;
        } else {
            self.refcounts.insert(id, rc - 1);
        }
        Ok(())
    }

    /// Read a full page.
    pub fn read(&self, id: PhysId) -> Result<Vec<u8>, AppError> {
        let path = self.page_path(id);
        let data = fs::read(&path).map_err(|e| {
            if e.kind() == std::io::ErrorKind::NotFound {
                AppError::Compute(format!("page file missing for live page {id}: {path:?}"))
            } else {
                AppError::from(e)
            }
        })?;
        if data.len() != self.page_size {
            return Err(AppError::Compute(format!(
                "page file corrupt for page {id}: expected {} bytes, got {}",
                self.page_size,
                data.len()
            )));
        }
        Ok(data)
    }

    /// Overwrite a page in place. Caller must guarantee exclusivity
    /// (refcount == 1); the store only checks the page exists.
    pub fn write_in_place(&mut self, id: PhysId, content: &[u8]) -> Result<(), AppError> {
        if !self.refcounts.contains_key(&id) {
            return Err(AppError::State(format!(
                "refcount anomaly: write_in_place on unknown page {id}"
            )));
        }
        if content.len() != self.page_size {
            return Err(AppError::Input(format!(
                "page content must be exactly {} bytes, got {}",
                self.page_size,
                content.len()
            )));
        }
        fs::write(self.page_path(id), content)?;
        Ok(())
    }

    /// Test/diagnostic hook: force a refcount value to exercise anomaly
    /// handling. Not used by the engine itself.
    #[doc(hidden)]
    pub fn debug_force_refcount(&mut self, id: PhysId, rc: u32) {
        self.refcounts.insert(id, rc);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn alloc_release_cycle() {
        let tmp = tempfile::tempdir().unwrap();
        let mut store = PageStore::create(tmp.path().to_path_buf(), 8, 2).unwrap();
        let a = store.alloc_with(&[1u8; 8]).unwrap();
        let b = store.alloc_with(&[2u8; 8]).unwrap();
        assert!(matches!(
            store.alloc_with(&[3u8; 8]),
            Err(AppError::Resource(_))
        ));
        store.add_ref(a).unwrap();
        store.release(a).unwrap();
        assert_eq!(store.refcount(a), Some(1));
        store.release(a).unwrap();
        assert_eq!(store.refcount(a), None);
        store.release(b).unwrap();
        assert!(matches!(store.release(b), Err(AppError::State(_))));
    }
}
