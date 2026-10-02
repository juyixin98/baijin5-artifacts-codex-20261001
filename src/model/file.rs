//! Per-file state: logical size plus the shared page cache.

use serde::Serialize;
use std::collections::BTreeMap;

/// One cached page: a full page-sized buffer plus its dirty mark.
#[derive(Debug, Clone)]
pub struct CachedPage {
    pub data: Vec<u8>,
    pub dirty: bool,
}

/// How much of a page is backed by the file at the current EOF.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PageValidity {
    /// The whole page lies within EOF.
    Full,
    /// Only the first n bytes lie within EOF (last partial page).
    Partial(u64),
    /// The page starts at or beyond EOF: any access is a SIGBUS analogue.
    BeyondEof,
}

/// The kernel-inode analogue: logical EOF + shared page cache.
#[derive(Debug)]
pub struct FileObject {
    pub path: String,
    pub size: u64,
    /// Shared page cache, keyed by page index. Shared mappings read/write
    /// these pages directly; private mappings copy them out on first write.
    pub pages: BTreeMap<u64, CachedPage>,
}

impl FileObject {
    pub fn new(path: &str, size: u64) -> Self {
        FileObject {
            path: path.to_string(),
            size,
            pages: BTreeMap::new(),
        }
    }

    pub fn validity(&self, page_idx: u64, page_size: u64) -> PageValidity {
        let start = page_idx * page_size;
        if start >= self.size {
            PageValidity::BeyondEof
        } else if start + page_size <= self.size {
            PageValidity::Full
        } else {
            PageValidity::Partial(self.size - start)
        }
    }

    /// Sorted dirty page indices within `[first, last]`.
    pub fn dirty_pages_in(&self, first: u64, last: u64) -> Vec<u64> {
        self.pages
            .range(first..=last)
            .filter(|(_, p)| p.dirty)
            .map(|(i, _)| *i)
            .collect()
    }
}

/// Diagnostic snapshot of one cached page.
#[derive(Debug, Clone, Serialize)]
pub struct PageInfo {
    pub index: u64,
    pub dirty: bool,
    /// Bytes within EOF for this page at the current file size.
    pub valid_bytes: u64,
}

/// Diagnostic snapshot of a file.
#[derive(Debug, Clone, Serialize)]
pub struct FileState {
    pub path: String,
    pub size: u64,
    pub pages: Vec<PageInfo>,
    pub dirty_pages: Vec<u64>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validity_boundaries() {
        let f = FileObject::new("f", 2 * 4096 + 100);
        assert_eq!(f.validity(0, 4096), PageValidity::Full);
        assert_eq!(f.validity(1, 4096), PageValidity::Full);
        assert_eq!(f.validity(2, 4096), PageValidity::Partial(100));
        assert_eq!(f.validity(3, 4096), PageValidity::BeyondEof);
    }

    #[test]
    fn zero_size_file_has_no_accessible_pages() {
        let f = FileObject::new("f", 0);
        assert_eq!(f.validity(0, 4096), PageValidity::BeyondEof);
    }
}
