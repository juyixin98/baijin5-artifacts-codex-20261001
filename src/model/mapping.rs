//! Mapping state: a range of a file projected into an address space,
//! either shared with the page cache or private (copy-on-write).

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub type MappingId = u64;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum MapKind {
    /// Writes go to the shared page cache, visible to every other shared
    /// mapping of the same file and written back on sync.
    Shared,
    /// Writes trigger copy-on-write into per-mapping anonymous pages and
    /// are never written back to the file.
    Private,
}

/// One live mapping. `private_pages` holds COW copies keyed by *file* page
/// index (not mapping-relative), so lookups are direct file-offset math.
#[derive(Debug)]
pub struct Mapping {
    pub id: MappingId,
    pub file: String,
    /// File offset the mapping starts at. Page-aligned (validated at map).
    pub offset: u64,
    pub length: u64,
    pub kind: MapKind,
    pub private_pages: BTreeMap<u64, Vec<u8>>,
}

impl Mapping {
    /// Inclusive file page index range covered by this mapping.
    pub fn page_range(&self, page_size: u64) -> (u64, u64) {
        let first = self.offset / page_size;
        let last = (self.offset + self.length - 1) / page_size;
        (first, last)
    }
}

/// Diagnostic snapshot of a mapping.
#[derive(Debug, Clone, Serialize)]
pub struct MappingState {
    pub id: MappingId,
    pub file: String,
    pub offset: u64,
    pub length: u64,
    pub kind: MapKind,
    pub private_pages: Vec<u64>,
}
