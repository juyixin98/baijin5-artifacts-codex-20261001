//! Backing page store: where page content lives when it is not cached.
//!
//! Two implementations:
//! - [`FilePageStore`]: one file per page under a directory
//!   (`page-<id>.bin`). This is the "local page access" fixture backend.
//! - [`MemStore`]: in-memory store for tests and embedded demos, with load
//!   failure injection to exercise the "undecidable" diagnostic path.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Mutex;

use crate::arc::PageId;

pub trait PageStore: Send + Sync {
    /// `Ok(None)` means the page does not exist in the store.
    fn load(&self, page: PageId) -> Result<Option<Vec<u8>>, String>;
}

/// Filesystem-backed store: `<dir>/page-<id>.bin`.
#[derive(Debug, Clone)]
pub struct FilePageStore {
    dir: PathBuf,
}

impl FilePageStore {
    pub fn new(dir: impl Into<PathBuf>) -> Self {
        FilePageStore { dir: dir.into() }
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }

    pub fn path_for(&self, page: PageId) -> PathBuf {
        // PageId is a u64, so the file name is always a plain integer —
        // no path traversal surface.
        self.dir.join(format!("page-{page}.bin"))
    }

    /// Persist a page (atomic via tmp file + rename).
    pub fn store(&self, page: PageId, data: &[u8]) -> Result<(), String> {
        std::fs::create_dir_all(&self.dir)
            .map_err(|e| format!("create {}: {e}", self.dir.display()))?;
        let tmp = self.dir.join(format!(".page-{page}.tmp"));
        std::fs::write(&tmp, data).map_err(|e| format!("write {}: {e}", tmp.display()))?;
        std::fs::rename(&tmp, self.path_for(page))
            .map_err(|e| format!("rename for page {page}: {e}"))
    }
}

impl PageStore for FilePageStore {
    fn load(&self, page: PageId) -> Result<Option<Vec<u8>>, String> {
        match std::fs::read(self.path_for(page)) {
            Ok(data) => Ok(Some(data)),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(e) => Err(format!("read page {page}: {e}")),
        }
    }
}

/// In-memory store with deterministic failure injection (test fixture).
/// Cloning shares the same underlying pages.
#[derive(Clone, Default)]
pub struct MemStore {
    pages: std::sync::Arc<Mutex<HashMap<PageId, Vec<u8>>>>,
    fail_loads: std::sync::Arc<AtomicBool>,
    loads: std::sync::Arc<AtomicU64>,
}

impl MemStore {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn put(&self, page: PageId, data: Vec<u8>) {
        self.pages.lock().unwrap().insert(page, data);
    }
    pub fn get(&self, page: PageId) -> Option<Vec<u8>> {
        self.pages.lock().unwrap().get(&page).cloned()
    }
    pub fn entries(&self) -> HashMap<PageId, Vec<u8>> {
        self.pages.lock().unwrap().clone()
    }
    /// When enabled, every load fails with an I/O-style error.
    pub fn set_fail_loads(&self, fail: bool) {
        self.fail_loads.store(fail, Ordering::SeqCst);
    }
    pub fn load_count(&self) -> u64 {
        self.loads.load(Ordering::SeqCst)
    }
}

impl PageStore for MemStore {
    fn load(&self, page: PageId) -> Result<Option<Vec<u8>>, String> {
        self.loads.fetch_add(1, Ordering::SeqCst);
        if self.fail_loads.load(Ordering::SeqCst) {
            return Err(format!("injected load failure on page {page}"));
        }
        Ok(self.pages.lock().unwrap().get(&page).cloned())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn file_store_roundtrip_and_missing() {
        let dir = tempfile::tempdir().unwrap();
        let store = FilePageStore::new(dir.path());
        assert_eq!(store.load(7).unwrap(), None);
        store.store(7, b"seven").unwrap();
        assert_eq!(store.load(7).unwrap(), Some(b"seven".to_vec()));
        store.store(7, b"SEVEN").unwrap();
        assert_eq!(store.load(7).unwrap(), Some(b"SEVEN".to_vec()));
    }

    #[test]
    fn mem_store_failure_injection() {
        let store = MemStore::new();
        store.put(1, b"x".to_vec());
        assert_eq!(store.load(1).unwrap(), Some(b"x".to_vec()));
        store.set_fail_loads(true);
        assert!(store.load(1).is_err());
    }
}
