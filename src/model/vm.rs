//! The runtime model: an in-memory analogue of mmap/msync/ftruncate/munmap.
//!
//! Semantics (documented in `docs/semantics.md`):
//!
//! - Access to a page lying wholly beyond EOF fails with
//!   `AccessOutOfRange` (SIGBUS analogue) — never a fabricated zero page.
//! - Shared writes dirty the shared page cache and are immediately visible
//!   to every other shared mapping of the file.
//! - Private writes copy the page out (COW) and never touch the cache or
//!   the persistent image.
//! - `sync` writes dirty cache pages back; a failed page keeps its dirty
//!   mark and the call reports `SyncFailed`.
//! - `truncate` discards cache pages wholly beyond the new EOF and zeroes
//!   the tail of the new last partial page.
//! - `unmap` drops private COW pages; shared dirty pages stay in the cache.

use super::file::{CachedPage, FileObject, FileState, PageInfo, PageValidity};
use super::mapping::{MapKind, Mapping, MappingId, MappingState};
use crate::config::ModelConfig;
use crate::error::{ErrorCategory, ModelError};
use crate::store::{BackingStore, StoreError};
use serde::Serialize;
use std::collections::HashMap;

/// Cumulative operation counters, exposed via the diagnostics API.
#[derive(Debug, Clone, Default, Serialize)]
pub struct Stats {
    pub read_faults: u64,
    pub write_faults: u64,
    pub cow_faults: u64,
    pub sync_calls: u64,
    pub sync_failures: u64,
    pub sigbus_errors: u64,
    pub truncates: u64,
    pub unmaps: u64,
}

/// Outcome of a successful `sync`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SyncOutcome {
    /// Cache pages written back and whose dirty mark was cleared.
    pub persisted: Vec<u64>,
    /// Dirty private COW pages in range — reported, never written back.
    pub skipped_private: Vec<u64>,
}

/// A cache page discarded by a shrinking truncate.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct DiscardedPage {
    pub index: u64,
    pub was_dirty: bool,
}

/// Outcome of a `truncate`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct TruncateReport {
    pub old_size: u64,
    pub new_size: u64,
    pub discarded_pages: Vec<DiscardedPage>,
    /// Last-partial page whose beyond-EOF tail was zeroed, if any.
    pub zeroed_tail_page: Option<u64>,
}

/// Outcome of an `unmap`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct UnmapReport {
    pub kind: MapKind,
    /// Dirty shared-cache pages in the unmapped range, still resident.
    pub dirty_pages_left_in_cache: Vec<u64>,
    /// Private COW pages dropped with the mapping.
    pub discarded_private_pages: Vec<u64>,
}

/// The virtual-memory model, generic over its backing store.
pub struct Vm<S: BackingStore> {
    cfg: ModelConfig,
    store: S,
    files: HashMap<String, FileObject>,
    mappings: HashMap<MappingId, Mapping>,
    next_mapping_id: MappingId,
    stats: Stats,
}

impl<S: BackingStore> Vm<S> {
    pub fn new(cfg: ModelConfig, store: S) -> Self {
        Vm {
            cfg,
            store,
            files: HashMap::new(),
            mappings: HashMap::new(),
            next_mapping_id: 1,
            stats: Stats::default(),
        }
    }

    pub fn stats(&self) -> Stats {
        self.stats.clone()
    }

    pub fn store(&self) -> &S {
        &self.store
    }

    // ---- file lifecycle -------------------------------------------------

    /// Create a new zero-filled file and register it with the model.
    pub fn create_file(&mut self, path: &str, size: u64) -> Result<(), ModelError> {
        if size > self.cfg.max_file_size {
            return Err(ModelError::new(
                ErrorCategory::InvalidArgument,
                format!(
                    "size {size} exceeds max_file_size {}",
                    self.cfg.max_file_size
                ),
            ));
        }
        if self.files.contains_key(path) || self.store.exists(path) {
            return Err(ModelError::new(
                ErrorCategory::Conflict,
                format!("file already exists: {path}"),
            ));
        }
        self.store.create(path, size).map_err(store_err)?;
        self.files
            .insert(path.to_string(), FileObject::new(path, size));
        Ok(())
    }

    /// Register an existing store file with the model (open(2) analogue).
    pub fn open_file(&mut self, path: &str) -> Result<u64, ModelError> {
        if let Some(f) = self.files.get(path) {
            return Ok(f.size);
        }
        let size = self.store.size(path).map_err(store_err)?;
        self.files
            .insert(path.to_string(), FileObject::new(path, size));
        Ok(size)
    }

    /// Direct write to the persistent image, bypassing the page cache.
    /// Models an external writer; intentionally not cache-coherent
    /// (documented in semantics.md). Extends the file like write(2).
    pub fn write_file_direct(
        &mut self,
        path: &str,
        offset: u64,
        data: &[u8],
    ) -> Result<(), ModelError> {
        self.store.write_at(path, offset, data).map_err(store_err)?;
        if let Some(f) = self.files.get_mut(path) {
            f.size = f.size.max(offset + data.len() as u64);
        }
        Ok(())
    }

    /// Read the persistent image directly from the store (bypasses cache).
    pub fn store_image(&self, path: &str, offset: u64, len: usize) -> Result<Vec<u8>, ModelError> {
        self.store.read_at(path, offset, len).map_err(store_err)
    }

    // ---- mapping lifecycle ----------------------------------------------

    pub fn map(
        &mut self,
        path: &str,
        offset: u64,
        length: u64,
        kind: MapKind,
    ) -> Result<MappingId, ModelError> {
        let ps = self.cfg.page_size;
        if !self.files.contains_key(path) {
            return Err(ModelError::new(
                ErrorCategory::NotFound,
                format!("no such file: {path}"),
            ));
        }
        if !offset.is_multiple_of(ps) {
            return Err(ModelError::new(
                ErrorCategory::InvalidArgument,
                format!("offset {offset} is not page-aligned (page_size {ps})"),
            ));
        }
        if length == 0 {
            return Err(ModelError::new(
                ErrorCategory::InvalidArgument,
                "length must be > 0",
            ));
        }
        if self.mappings.len() >= self.cfg.max_mappings {
            return Err(ModelError::new(
                ErrorCategory::InvalidArgument,
                "mapping limit reached",
            ));
        }
        let id = self.next_mapping_id;
        self.next_mapping_id += 1;
        self.mappings.insert(
            id,
            Mapping {
                id,
                file: path.to_string(),
                offset,
                length,
                kind,
                private_pages: Default::default(),
            },
        );
        Ok(id)
    }

    pub fn unmap(&mut self, id: MappingId) -> Result<UnmapReport, ModelError> {
        let m = self.mappings.remove(&id).ok_or_else(|| {
            ModelError::new(ErrorCategory::NotFound, format!("no such mapping: {id}"))
        })?;
        let (first, last) = m.page_range(self.cfg.page_size);
        let dirty_left = match m.kind {
            MapKind::Shared => self
                .files
                .get(&m.file)
                .map(|f| f.dirty_pages_in(first, last))
                .unwrap_or_default(),
            MapKind::Private => Vec::new(),
        };
        self.stats.unmaps += 1;
        Ok(UnmapReport {
            kind: m.kind,
            dirty_pages_left_in_cache: dirty_left,
            discarded_private_pages: m.private_pages.keys().copied().collect(),
        })
    }

    // ---- reads and writes ------------------------------------------------

    pub fn read(&mut self, id: MappingId, addr: u64, len: usize) -> Result<Vec<u8>, ModelError> {
        let (file, base, length, kind) = self.mapping_shape(id)?;
        self.check_window(base, length, addr, len as u64)?;
        let ps = self.cfg.page_size;
        let mut out = vec![0u8; len];
        let mut done = 0u64;
        while done < len as u64 {
            let foff = base + addr + done;
            let page_idx = foff / ps;
            let in_page = (foff % ps) as usize;
            let n = ((ps as usize) - in_page).min(len - done as usize);
            self.check_page_accessible(&file, page_idx)?;

            // Private COW copy wins over the shared cache.
            let private_chunk = match kind {
                MapKind::Private => self
                    .mappings
                    .get(&id)
                    .and_then(|m| m.private_pages.get(&page_idx))
                    .map(|p| p[in_page..in_page + n].to_vec()),
                MapKind::Shared => None,
            };
            let chunk = match private_chunk {
                Some(c) => c,
                None => {
                    self.ensure_cache_page(&file, page_idx)?;
                    let page = &self.files[&file].pages[&page_idx];
                    page.data[in_page..in_page + n].to_vec()
                }
            };
            out[done as usize..done as usize + n].copy_from_slice(&chunk);
            done += n as u64;
        }
        Ok(out)
    }

    pub fn write(&mut self, id: MappingId, addr: u64, data: &[u8]) -> Result<(), ModelError> {
        let (file, base, length, kind) = self.mapping_shape(id)?;
        self.check_window(base, length, addr, data.len() as u64)?;
        let ps = self.cfg.page_size;
        let mut done = 0u64;
        while done < data.len() as u64 {
            let foff = base + addr + done;
            let page_idx = foff / ps;
            let in_page = (foff % ps) as usize;
            let n = ((ps as usize) - in_page).min(data.len() - done as usize);
            self.check_page_accessible(&file, page_idx)?;
            let chunk = &data[done as usize..done as usize + n];
            match kind {
                MapKind::Shared => self.write_shared(&file, page_idx, in_page, chunk)?,
                MapKind::Private => self.write_private(id, &file, page_idx, in_page, chunk)?,
            }
            self.stats.write_faults += 1;
            done += n as u64;
        }
        Ok(())
    }

    fn write_shared(
        &mut self,
        file: &str,
        page_idx: u64,
        in_page: usize,
        chunk: &[u8],
    ) -> Result<(), ModelError> {
        self.ensure_cache_page(file, page_idx)?;
        let page = self
            .files
            .get_mut(file)
            .and_then(|f| f.pages.get_mut(&page_idx))
            .expect("cache page present after fault-in");
        page.data[in_page..in_page + chunk.len()].copy_from_slice(chunk);
        page.dirty = true;
        Ok(())
    }

    fn write_private(
        &mut self,
        id: MappingId,
        file: &str,
        page_idx: u64,
        in_page: usize,
        chunk: &[u8],
    ) -> Result<(), ModelError> {
        let has_private = self.mappings[&id].private_pages.contains_key(&page_idx);
        if !has_private {
            // COW: snapshot the shared content, leave the cache untouched.
            self.ensure_cache_page(file, page_idx)?;
            let snapshot = self.files[file].pages[&page_idx].data.clone();
            self.mappings
                .get_mut(&id)
                .expect("mapping exists")
                .private_pages
                .insert(page_idx, snapshot);
            self.stats.cow_faults += 1;
        }
        let page = self
            .mappings
            .get_mut(&id)
            .and_then(|m| m.private_pages.get_mut(&page_idx))
            .expect("private page present after COW");
        page[in_page..in_page + chunk.len()].copy_from_slice(chunk);
        Ok(())
    }

    // ---- sync ------------------------------------------------------------

    /// msync analogue. Shared mappings write dirty cache pages in range back
    /// to the store; any page that fails keeps its dirty mark and the call
    /// returns `SyncFailed`. Private mappings never write back.
    pub fn sync(&mut self, id: MappingId) -> Result<SyncOutcome, ModelError> {
        self.stats.sync_calls += 1;
        let (file, base, length, kind) = self.mapping_shape(id)?;
        let ps = self.cfg.page_size;
        let first = base / ps;
        let last = (base + length - 1) / ps;

        if kind == MapKind::Private {
            let skipped = self.mappings[&id]
                .private_pages
                .range(first..=last)
                .map(|(i, _)| *i)
                .collect();
            return Ok(SyncOutcome {
                persisted: Vec::new(),
                skipped_private: skipped,
            });
        }

        let size = self.files[&file].size;
        let dirty = self.files[&file].dirty_pages_in(first, last);
        let mut persisted = Vec::new();
        let mut failed = Vec::new();
        let mut last_err = String::new();
        for idx in dirty {
            let page_start = idx * ps;
            // Only bytes within EOF are written back; a beyond-EOF tail
            // written earlier stays cache-resident until a grow makes it
            // valid (documented in semantics.md).
            let persist_len = (ps.min(size - page_start)) as usize;
            let data = self.files[&file].pages[&idx].data[..persist_len].to_vec();
            match self.store.write_at(&file, page_start, &data) {
                Ok(()) => {
                    if let Some(p) = self
                        .files
                        .get_mut(&file)
                        .and_then(|f| f.pages.get_mut(&idx))
                    {
                        p.dirty = false;
                    }
                    persisted.push(idx);
                }
                Err(e) => {
                    last_err = e.to_string();
                    failed.push(idx); // dirty mark intentionally retained
                }
            }
        }
        if let Err(e) = self.store.flush(&file) {
            last_err = e.to_string();
            failed.push(u64::MAX); // sentinel: flush (not a page) failed
        }
        if !failed.is_empty() {
            self.stats.sync_failures += 1;
            return Err(ModelError::sync_failed(
                failed,
                format!("sync of {file} incomplete: {last_err}"),
            ));
        }
        Ok(SyncOutcome {
            persisted,
            skipped_private: Vec::new(),
        })
    }

    // ---- truncate ---------------------------------------------------------

    /// ftruncate analogue. Shrinking discards cache pages wholly beyond the
    /// new EOF (dirty or not) and zeroes the beyond-EOF tail of the new last
    /// partial page. Growing only moves EOF; new pages fault in as zeros
    /// from the sparse store.
    pub fn truncate(&mut self, path: &str, new_size: u64) -> Result<TruncateReport, ModelError> {
        if new_size > self.cfg.max_file_size {
            return Err(ModelError::new(
                ErrorCategory::InvalidArgument,
                format!("size {new_size} exceeds max_file_size"),
            ));
        }
        if !self.files.contains_key(path) {
            return Err(ModelError::new(
                ErrorCategory::NotFound,
                format!("no such file: {path}"),
            ));
        }
        self.store.truncate(path, new_size).map_err(store_err)?;
        let ps = self.cfg.page_size;
        let file = self.files.get_mut(path).expect("checked above");
        let old_size = file.size;
        let mut report = TruncateReport {
            old_size,
            new_size,
            discarded_pages: Vec::new(),
            zeroed_tail_page: None,
        };
        if new_size < old_size {
            let first_dead = new_size.div_ceil(ps);
            let dead: Vec<u64> = file.pages.range(first_dead..).map(|(i, _)| *i).collect();
            for idx in dead {
                let p = file.pages.remove(&idx).expect("present");
                report.discarded_pages.push(DiscardedPage {
                    index: idx,
                    was_dirty: p.dirty,
                });
            }
            if !new_size.is_multiple_of(ps) {
                let tail_idx = new_size / ps;
                if let Some(p) = file.pages.get_mut(&tail_idx) {
                    let keep = (new_size % ps) as usize;
                    p.data[keep..].fill(0);
                    report.zeroed_tail_page = Some(tail_idx);
                }
            }
        }
        file.size = new_size;
        self.stats.truncates += 1;
        Ok(report)
    }

    // ---- diagnostics -------------------------------------------------------

    pub fn file_state(&self, path: &str) -> Result<FileState, ModelError> {
        let f = self.files.get(path).ok_or_else(|| {
            ModelError::new(ErrorCategory::NotFound, format!("no such file: {path}"))
        })?;
        let ps = self.cfg.page_size;
        let pages: Vec<PageInfo> = f
            .pages
            .iter()
            .map(|(i, p)| PageInfo {
                index: *i,
                dirty: p.dirty,
                valid_bytes: match f.validity(*i, ps) {
                    PageValidity::Full => ps,
                    PageValidity::Partial(n) => n,
                    PageValidity::BeyondEof => 0,
                },
            })
            .collect();
        let dirty_pages = pages.iter().filter(|p| p.dirty).map(|p| p.index).collect();
        Ok(FileState {
            path: f.path.clone(),
            size: f.size,
            pages,
            dirty_pages,
        })
    }

    pub fn mapping_state(&self, id: MappingId) -> Result<MappingState, ModelError> {
        let m = self.mappings.get(&id).ok_or_else(|| {
            ModelError::new(ErrorCategory::NotFound, format!("no such mapping: {id}"))
        })?;
        Ok(MappingState {
            id: m.id,
            file: m.file.clone(),
            offset: m.offset,
            length: m.length,
            kind: m.kind,
            private_pages: m.private_pages.keys().copied().collect(),
        })
    }

    // ---- internals ---------------------------------------------------------

    fn mapping_shape(&self, id: MappingId) -> Result<(String, u64, u64, MapKind), ModelError> {
        let m = self.mappings.get(&id).ok_or_else(|| {
            ModelError::new(ErrorCategory::NotFound, format!("no such mapping: {id}"))
        })?;
        Ok((m.file.clone(), m.offset, m.length, m.kind))
    }

    /// The accessed window must lie inside the mapping.
    fn check_window(&self, base: u64, length: u64, addr: u64, len: u64) -> Result<(), ModelError> {
        if len == 0 {
            return Err(ModelError::new(
                ErrorCategory::InvalidArgument,
                "zero-length access",
            ));
        }
        if addr.checked_add(len).is_none_or(|end| end > length) {
            return Err(ModelError::new(
                ErrorCategory::InvalidArgument,
                format!(
                    "access [{addr}, {}) outside mapping of length {length}",
                    addr + len
                ),
            ));
        }
        let _ = base;
        Ok(())
    }

    /// SIGBUS analogue: a page wholly beyond EOF is an error, never zeros.
    fn check_page_accessible(&mut self, file: &str, page_idx: u64) -> Result<(), ModelError> {
        let f = self.files.get(file).ok_or_else(|| {
            ModelError::new(ErrorCategory::NotFound, format!("no such file: {file}"))
        })?;
        if f.validity(page_idx, self.cfg.page_size) == PageValidity::BeyondEof {
            self.stats.sigbus_errors += 1;
            return Err(ModelError::new(
                ErrorCategory::AccessOutOfRange,
                format!(
                    "page {page_idx} (file offsets {}..{}) lies wholly beyond EOF {}",
                    page_idx * self.cfg.page_size,
                    (page_idx + 1) * self.cfg.page_size,
                    f.size
                ),
            ));
        }
        Ok(())
    }

    /// Fault a page into the shared cache from the store. The store is
    /// sparse, so bytes past its physical end legitimately read as zeros.
    fn ensure_cache_page(&mut self, file: &str, page_idx: u64) -> Result<(), ModelError> {
        let f = self.files.get(file).ok_or_else(|| {
            ModelError::new(ErrorCategory::NotFound, format!("no such file: {file}"))
        })?;
        if f.pages.contains_key(&page_idx) {
            return Ok(());
        }
        let ps = self.cfg.page_size;
        let page_start = page_idx * ps;
        let read_len = (ps.min(f.size - page_start)) as usize;
        let got = self
            .store
            .read_at(file, page_start, read_len)
            .map_err(store_err)?;
        if got.len() > read_len {
            return Err(ModelError::new(
                ErrorCategory::StoreUnavailable,
                "store returned more bytes than requested",
            ));
        }
        let mut data = vec![0u8; ps as usize];
        data[..got.len()].copy_from_slice(&got);
        self.files
            .get_mut(file)
            .expect("checked above")
            .pages
            .insert(page_idx, CachedPage { data, dirty: false });
        self.stats.read_faults += 1;
        Ok(())
    }
}

fn store_err(e: StoreError) -> ModelError {
    match e {
        StoreError::NotFound(p) => {
            ModelError::new(ErrorCategory::NotFound, format!("no such file: {p}"))
        }
        other => ModelError::new(ErrorCategory::StoreUnavailable, other.to_string()),
    }
}
