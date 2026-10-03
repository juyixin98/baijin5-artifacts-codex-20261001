//! Core ARC algorithm (Megiddo & Modha, FAST '03) with dirty-page write-back.
//!
//! Lists:
//! - `T1`: resident pages referenced exactly once recently (LRU order).
//! - `T2`: resident pages referenced at least twice recently.
//! - `B1`: ghost entries for pages evicted from `T1` (metadata only, NO data).
//! - `B2`: ghost entries for pages evicted from `T2` (metadata only, NO data).
//!
//! Invariants (enforced after every mutation, see [`ArcCache::check_invariants`]):
//! - `|T1| + |T2| <= c`            (resident pages fit in capacity)
//! - `|T1| + |B1| <= c`            (L1 fits in capacity)
//! - `|T1|+|T2|+|B1|+|B2| <= 2c`   (all history fits in 2c)
//! - `0 <= p <= c`
//!
//! Key semantic decisions (documented in README.md):
//! 1. A ghost hit is NOT a data hit: the page content is fetched from the
//!    backing store and counted separately (`ghost_hits_b1/b2`).
//! 2. Miss case IV(i) with `|T1| == c`: the LRU page of T1 is dropped from
//!    the cache entirely (it does NOT join B1), matching the paper.
//! 3. `p` adapts in integer steps: on a B1 hit
//!    `p = min(c, p + max(|B2| / |B1|, 1))`, on a B2 hit
//!    `p = p - max(|B1| / |B2|, 1)` (saturating at 0), using integer division.
//! 4. Capacity zero is defined: reads are read-through, writes are
//!    write-through, no list is ever touched.
//! 5. Grow corner (dynamic resize only): on a miss, a real page is only
//!    evicted when the resident set is actually full (`|T1|+|T2| >= c`). With
//!    a fixed capacity this guard is a no-op (residency is sticky once full);
//!    it only matters after a capacity increase with ghosts present.
//! 6. Dirty victims are written back through the injected [`Writeback`]
//!    adapter BEFORE any list is mutated. A write-back failure aborts the
//!    whole access: lists, `p` and page data are left untouched.

use std::collections::{HashMap, HashSet, VecDeque};

use crate::error::ArcError;
use crate::store::PageStore;
use crate::writeback::Writeback;

pub type PageId = u64;

/// A resident page.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Page {
    pub data: Vec<u8>,
    pub dirty: bool,
}

/// Where a page id currently lives.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Class {
    T1,
    T2,
    B1,
    B2,
    Absent,
}

/// Outcome of a successful access. Ghost hits are distinct from real hits:
/// the data was NOT cached and had to be fetched.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Outcome {
    HitT1,
    HitT2,
    GhostHitB1,
    GhostHitB2,
    /// Absent page fetched and inserted into T1.
    MissFill,
    /// Capacity is zero: read served straight from the store, nothing cached.
    ReadThrough,
    /// Capacity is zero: write pushed straight through the write-back adapter.
    WriteThrough,
}

/// Destination of an evicted resident page.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum GhostDest {
    /// T1 victim joins B1.
    B1,
    /// T2 victim joins B2.
    B2,
    /// Dropped entirely (paper case IV(i), |T1| == c): no ghost entry.
    None,
}

/// One planned eviction. Victims are computed before any mutation so a
/// write-back failure can abort the access cleanly.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case", tag = "kind")]
pub enum Victim {
    /// A resident page leaves the cache (written back first if dirty).
    Real { page: PageId, dirty: bool, dest: GhostDest },
    /// A ghost entry is discarded (no data involved).
    Ghost { page: PageId, from_b2: bool },
}

/// What happened during one access, for diagnostics.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AccessReport {
    pub outcome: Outcome,
    /// True when page content was loaded from the backing store during this
    /// access (miss or ghost hit). Ghost hits always have `fetched == true`.
    pub fetched: bool,
    pub victims: Vec<Victim>,
}

/// Result of a resize.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResizeReport {
    pub old_capacity: usize,
    pub new_capacity: usize,
    pub victims: Vec<Victim>,
}

/// Cumulative counters. Compared field-by-field against the independent
/// model in the parity test.
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct Stats {
    pub reads: u64,
    pub writes: u64,
    pub hits_t1: u64,
    pub hits_t2: u64,
    pub ghost_hits_b1: u64,
    pub ghost_hits_b2: u64,
    pub misses: u64,
    pub read_throughs: u64,
    pub write_throughs: u64,
    pub evictions_clean: u64,
    pub evictions_dirty: u64,
    pub dropped_t1: u64,
    pub ghost_evictions: u64,
    pub writebacks: u64,
    pub writeback_failures: u64,
    pub store_fetches: u64,
    pub store_misses: u64,
}

/// LRU list with O(1) membership. Front is LRU, back is MRU.
#[derive(Debug, Clone, Default)]
struct LruList {
    order: VecDeque<PageId>,
    set: HashSet<PageId>,
}

impl LruList {
    fn contains(&self, id: PageId) -> bool {
        self.set.contains(&id)
    }
    fn len(&self) -> usize {
        self.order.len()
    }
    fn is_empty(&self) -> bool {
        self.order.is_empty()
    }
    fn push_mru(&mut self, id: PageId) {
        debug_assert!(!self.set.contains(&id));
        self.order.push_back(id);
        self.set.insert(id);
    }
    fn remove(&mut self, id: PageId) -> bool {
        if !self.set.remove(&id) {
            return false;
        }
        if let Some(pos) = self.order.iter().position(|&x| x == id) {
            self.order.remove(pos);
        }
        true
    }
    fn lru(&self) -> Option<PageId> {
        self.order.front().copied()
    }
    fn as_vec(&self) -> Vec<PageId> {
        self.order.iter().copied().collect()
    }
}

/// Snapshot of the four lists (LRU -> MRU), for diagnostics and tests.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct ListSnapshot {
    pub t1: Vec<PageId>,
    pub t2: Vec<PageId>,
    pub b1: Vec<PageId>,
    pub b2: Vec<PageId>,
}

pub struct ArcCache {
    capacity: usize,
    p: usize,
    t1: LruList,
    t2: LruList,
    b1: LruList,
    b2: LruList,
    pages: HashMap<PageId, Page>,
    stats: Stats,
}

impl ArcCache {
    pub fn new(capacity: usize) -> Self {
        ArcCache {
            capacity,
            p: 0,
            t1: LruList::default(),
            t2: LruList::default(),
            b1: LruList::default(),
            b2: LruList::default(),
            pages: HashMap::new(),
            stats: Stats::default(),
        }
    }

    /// Rebuild a cache from a snapshot (see `state.rs`). `pages` must contain
    /// exactly the ids listed in `t1`/`t2`; ghosts carry no data.
    pub fn from_parts(
        capacity: usize,
        p: usize,
        lists: ListSnapshot,
        pages: HashMap<PageId, Page>,
        stats: Stats,
    ) -> Result<Self, ArcError> {
        let mut cache = ArcCache::new(capacity);
        cache.p = p;
        for id in &lists.t1 {
            cache.t1.push_mru(*id);
        }
        for id in &lists.t2 {
            cache.t2.push_mru(*id);
        }
        for id in &lists.b1 {
            cache.b1.push_mru(*id);
        }
        for id in &lists.b2 {
            cache.b2.push_mru(*id);
        }
        cache.pages = pages;
        cache.stats = stats;
        cache.check_invariants();
        Ok(cache)
    }

    pub fn capacity(&self) -> usize {
        self.capacity
    }
    pub fn p(&self) -> usize {
        self.p
    }
    pub fn stats(&self) -> &Stats {
        &self.stats
    }
    pub fn lists(&self) -> ListSnapshot {
        ListSnapshot {
            t1: self.t1.as_vec(),
            t2: self.t2.as_vec(),
            b1: self.b1.as_vec(),
            b2: self.b2.as_vec(),
        }
    }
    pub fn resident_len(&self) -> usize {
        self.t1.len() + self.t2.len()
    }
    pub fn dirty_pages(&self) -> Vec<PageId> {
        let mut ids: Vec<PageId> = self
            .pages
            .iter()
            .filter(|(_, p)| p.dirty)
            .map(|(id, _)| *id)
            .collect();
        ids.sort_unstable();
        ids
    }
    /// True only when the page CONTENT is cached. A page present in B1/B2 is
    /// not cached — this is the "ghost hit != data" boundary.
    pub fn contains_data(&self, id: PageId) -> bool {
        self.pages.contains_key(&id)
    }
    pub fn classify(&self, id: PageId) -> Class {
        if self.t1.contains(id) {
            Class::T1
        } else if self.t2.contains(id) {
            Class::T2
        } else if self.b1.contains(id) {
            Class::B1
        } else if self.b2.contains(id) {
            Class::B2
        } else {
            Class::Absent
        }
    }

    /// Read a page. On miss/ghost the content is fetched via `store`.
    pub fn read(
        &mut self,
        id: PageId,
        store: &dyn PageStore,
        wb: &dyn Writeback,
    ) -> Result<(Vec<u8>, AccessReport), ArcError> {
        self.stats.reads += 1;
        let class = self.classify(id);

        if self.capacity == 0 {
            // Defined zero-capacity behaviour: read-through, no list touched.
            return match store.load(id) {
                Ok(Some(data)) => {
                    self.stats.read_throughs += 1;
                    self.stats.store_fetches += 1;
                    Ok((
                        data,
                        AccessReport {
                            outcome: Outcome::ReadThrough,
                            fetched: true,
                            victims: vec![],
                        },
                    ))
                }
                Ok(None) => {
                    self.stats.store_misses += 1;
                    Err(ArcError::PageNotFound { page: id })
                }
                Err(e) => Err(ArcError::Store {
                    page: id,
                    message: e,
                }),
            };
        }

        // Fetch phase: happens BEFORE any mutation so a store failure leaves
        // the cache untouched.
        let fetched = match class {
            Class::T1 | Class::T2 => None,
            Class::B1 | Class::B2 | Class::Absent => match store.load(id) {
                Ok(Some(data)) => {
                    self.stats.store_fetches += 1;
                    Some(data)
                }
                Ok(None) => {
                    self.stats.store_misses += 1;
                    return Err(ArcError::PageNotFound { page: id });
                }
                Err(e) => {
                    return Err(ArcError::Store {
                        page: id,
                        message: e,
                    })
                }
            },
        };

        let report = self.admit(id, class, fetched, None, wb)?;
        let data = self
            .pages
            .get(&id)
            .map(|p| p.data.clone())
            .expect("page resident after successful read");
        Ok((data, report))
    }

    /// Write a full page. The page becomes dirty in the cache; it reaches the
    /// backing store only through the write-back adapter (eviction, resize,
    /// flush) or, at capacity zero, immediately (write-through).
    pub fn write(
        &mut self,
        id: PageId,
        data: Vec<u8>,
        wb: &dyn Writeback,
    ) -> Result<AccessReport, ArcError> {
        self.stats.writes += 1;
        let class = self.classify(id);

        if self.capacity == 0 {
            wb.writeback(id, &data)
                .map_err(|e| ArcError::Writeback {
                    page: id,
                    message: e,
                })
                .inspect_err(|_| self.stats.writeback_failures += 1)?;
            self.stats.write_throughs += 1;
            return Ok(AccessReport {
                outcome: Outcome::WriteThrough,
                fetched: false,
                victims: vec![],
            });
        }

        self.admit(id, class, None, Some(data), wb)
    }

    /// Shared admission path for reads and writes (capacity > 0).
    ///
    /// `fetched` is the content loaded from the store (reads of non-resident
    /// pages); `written` is the new content for writes. Exactly one of them
    /// is `Some` when the page is not already resident.
    fn admit(
        &mut self,
        id: PageId,
        class: Class,
        fetched: Option<Vec<u8>>,
        written: Option<Vec<u8>>,
        wb: &dyn Writeback,
    ) -> Result<AccessReport, ArcError> {
        // 1. Adapt p (computed, not yet applied — rolled back on failure).
        let p_eff = match class {
            Class::B1 => {
                let delta = (self.b2.len() / self.b1.len().max(1)).max(1);
                (self.p + delta).min(self.capacity)
            }
            Class::B2 => {
                let delta = (self.b1.len() / self.b2.len().max(1)).max(1);
                self.p.saturating_sub(delta)
            }
            _ => self.p,
        };

        // 2. Plan victims (pure).
        let victims = self.plan_victims(class, p_eff);

        // 3. Write back dirty victims BEFORE mutating anything.
        for victim in &victims {
            if let Victim::Real { page, dirty: true, .. } = victim {
                let data = &self.pages[page].data;
                wb.writeback(*page, data).map_err(|e| {
                    self.stats.writeback_failures += 1;
                    ArcError::Writeback {
                        page: *page,
                        message: e,
                    }
                })?;
                self.stats.writebacks += 1;
            }
        }

        // 4. Apply: p, victim moves, insertion/promotion.
        self.p = p_eff;
        for victim in &victims {
            self.apply_victim(*victim);
        }

        let is_write = written.is_some();
        let outcome = match class {
            Class::T1 => {
                self.t1.remove(id);
                self.t2.push_mru(id);
                self.stats.hits_t1 += 1;
                Outcome::HitT1
            }
            Class::T2 => {
                self.t2.remove(id);
                self.t2.push_mru(id);
                self.stats.hits_t2 += 1;
                Outcome::HitT2
            }
            Class::B1 => {
                self.b1.remove(id);
                let page = self.materialize(id, fetched, written.clone(), is_write);
                self.t2.push_mru(id);
                self.pages.insert(id, page);
                self.stats.ghost_hits_b1 += 1;
                Outcome::GhostHitB1
            }
            Class::B2 => {
                self.b2.remove(id);
                let page = self.materialize(id, fetched, written.clone(), is_write);
                self.t2.push_mru(id);
                self.pages.insert(id, page);
                self.stats.ghost_hits_b2 += 1;
                Outcome::GhostHitB2
            }
            Class::Absent => {
                let page = self.materialize(id, fetched, written.clone(), is_write);
                self.t1.push_mru(id);
                self.pages.insert(id, page);
                self.stats.misses += 1;
                Outcome::MissFill
            }
        };

        // A write always (re)sets the content and marks the page dirty,
        // including on the hit path where no materialize() ran.
        if let Some(new_data) = written {
            let page = self
                .pages
                .get_mut(&id)
                .expect("page resident after admit");
            page.data = new_data;
            page.dirty = true;
        }

        self.check_invariants();
        Ok(AccessReport {
            outcome,
            fetched: matches!(class, Class::B1 | Class::B2 | Class::Absent) && !is_write,
            victims,
        })
    }

    fn materialize(
        &self,
        id: PageId,
        fetched: Option<Vec<u8>>,
        written: Option<Vec<u8>>,
        is_write: bool,
    ) -> Page {
        let _ = id;
        let data = written.or(fetched).unwrap_or_default();
        Page {
            data,
            dirty: is_write,
        }
    }

    /// Pure victim planning. See module docs for the exact rules.
    fn plan_victims(&self, class: Class, p_eff: usize) -> Vec<Victim> {
        let c = self.capacity;
        let mut victims = Vec::new();
        match class {
            Class::T1 | Class::T2 => {}
            Class::B1 | Class::B2 => {
                // Grow-corner guard: only evict a resident when residents are
                // full. No-op under fixed capacity (residency is sticky).
                if self.resident_len() >= c {
                    if let Some(v) = self.replace_plan(matches!(class, Class::B2), p_eff) {
                        victims.push(v);
                    }
                }
            }
            Class::Absent => {
                let l1 = self.t1.len() + self.b1.len();
                let total = l1 + self.t2.len() + self.b2.len();
                if l1 == c {
                    if self.t1.len() < c {
                        // B1 is guaranteed non-empty here.
                        if let Some(g) = self.b1.lru() {
                            victims.push(Victim::Ghost {
                                page: g,
                                from_b2: false,
                            });
                        }
                        if let Some(v) = self.replace_plan(false, p_eff) {
                            victims.push(v);
                        }
                    } else {
                        // |T1| == c, B1 empty: drop T1 LRU entirely (paper
                        // case IV(i) else-branch — no ghost entry).
                        if let Some(v) = self.t1.lru() {
                            victims.push(self.real_victim(v, GhostDest::None));
                        }
                    }
                } else if total >= c {
                    if total == 2 * c {
                        if let Some(g) = self.b2.lru() {
                            victims.push(Victim::Ghost {
                                page: g,
                                from_b2: true,
                            });
                        }
                    }
                    // Grow-corner guard (see module docs, rule 5).
                    if self.resident_len() >= c {
                        if let Some(v) = self.replace_plan(false, p_eff) {
                            victims.push(v);
                        }
                    }
                }
            }
        }
        victims
    }

    /// REPLACE(x, p) from the paper, planned (not applied).
    fn replace_plan(&self, x_in_b2: bool, p_eff: usize) -> Option<Victim> {
        let use_t1 = !self.t1.is_empty()
            && ((x_in_b2 && self.t1.len() == p_eff) || self.t1.len() > p_eff);
        if use_t1 {
            return self.t1.lru().map(|v| self.real_victim(v, GhostDest::B1));
        }
        if !self.t2.is_empty() {
            return self.t2.lru().map(|v| self.real_victim(v, GhostDest::B2));
        }
        // Defensive fallback (unreachable under the paper's invariants).
        self.t1.lru().map(|v| self.real_victim(v, GhostDest::B1))
    }

    fn real_victim(&self, id: PageId, dest: GhostDest) -> Victim {
        let dirty = self.pages.get(&id).map(|p| p.dirty).unwrap_or(false);
        Victim::Real {
            page: id,
            dirty,
            dest,
        }
    }

    fn apply_victim(&mut self, victim: Victim) {
        match victim {
            Victim::Real { page, dirty, dest } => {
                self.t1.remove(page);
                self.t2.remove(page);
                self.pages.remove(&page);
                match dest {
                    GhostDest::B1 => self.b1.push_mru(page),
                    GhostDest::B2 => self.b2.push_mru(page),
                    GhostDest::None => {
                        self.stats.dropped_t1 += 1;
                    }
                }
                if dirty {
                    self.stats.evictions_dirty += 1;
                } else {
                    self.stats.evictions_clean += 1;
                }
            }
            Victim::Ghost { page, from_b2 } => {
                if from_b2 {
                    self.b2.remove(page);
                } else {
                    self.b1.remove(page);
                }
                self.stats.ghost_evictions += 1;
            }
        }
    }

    /// Dynamic resize. Atomic with respect to write-back: victims are planned
    /// and written back before any list or the capacity itself changes; a
    /// write-back failure leaves the old capacity and all state untouched.
    pub fn resize(
        &mut self,
        new_capacity: usize,
        wb: &dyn Writeback,
    ) -> Result<ResizeReport, ArcError> {
        let old = self.capacity;
        if new_capacity == old {
            return Ok(ResizeReport {
                old_capacity: old,
                new_capacity,
                victims: vec![],
            });
        }
        let p_eff = self.p.min(new_capacity);

        // Simulate on plain vectors (front = LRU).
        let mut t1 = self.t1.as_vec();
        let mut t2 = self.t2.as_vec();
        let mut b1 = self.b1.as_vec();
        let mut b2 = self.b2.as_vec();
        let mut victims: Vec<Victim> = Vec::new();

        // 1. Trim residents to the new capacity, REPLACE-style preference.
        while t1.len() + t2.len() > new_capacity {
            if !t1.is_empty() && (t1.len() > p_eff || t2.is_empty()) {
                let v = t1.remove(0);
                victims.push(self.real_victim(v, GhostDest::B1));
                b1.push(v);
            } else {
                let v = t2.remove(0);
                victims.push(self.real_victim(v, GhostDest::B2));
                b2.push(v);
            }
        }
        // 2. Restore the L1 invariant.
        while t1.len() + b1.len() > new_capacity {
            let g = b1.remove(0);
            victims.push(Victim::Ghost {
                page: g,
                from_b2: false,
            });
        }
        // 3. Restore the 2c total invariant.
        while t1.len() + t2.len() + b1.len() + b2.len() > 2 * new_capacity && !b2.is_empty() {
            let g = b2.remove(0);
            victims.push(Victim::Ghost {
                page: g,
                from_b2: true,
            });
        }

        // Write back dirty victims before mutating.
        for victim in &victims {
            if let Victim::Real { page, dirty: true, .. } = victim {
                let data = &self.pages[page].data;
                wb.writeback(*page, data).map_err(|e| {
                    self.stats.writeback_failures += 1;
                    ArcError::Writeback {
                        page: *page,
                        message: e,
                    }
                })?;
                self.stats.writebacks += 1;
            }
        }

        self.capacity = new_capacity;
        self.p = p_eff;
        for victim in &victims {
            self.apply_victim(*victim);
        }
        self.check_invariants();
        Ok(ResizeReport {
            old_capacity: old,
            new_capacity,
            victims,
        })
    }

    /// Write back every dirty page (sorted by id for determinism). Used
    /// before snapshots. On failure, pages written so far stay clean in the
    /// store and the error is returned.
    pub fn flush_dirty(&mut self, wb: &dyn Writeback) -> Result<Vec<PageId>, ArcError> {
        let ids = self.dirty_pages();
        let mut flushed = Vec::new();
        for id in ids {
            let data = self.pages[&id].data.clone();
            wb.writeback(id, &data).map_err(|e| {
                self.stats.writeback_failures += 1;
                ArcError::Writeback {
                    page: id,
                    message: e,
                }
            })?;
            self.stats.writebacks += 1;
            if let Some(page) = self.pages.get_mut(&id) {
                page.dirty = false;
            }
            flushed.push(id);
        }
        Ok(flushed)
    }

    /// Debug assertion of the documented invariants.
    pub fn check_invariants(&self) {
        let c = self.capacity;
        debug_assert!(self.t1.len() + self.t2.len() <= c, "residents exceed capacity");
        debug_assert!(self.t1.len() + self.b1.len() <= c, "L1 exceeds capacity");
        debug_assert!(
            self.t1.len() + self.t2.len() + self.b1.len() + self.b2.len() <= 2 * c,
            "total history exceeds 2c"
        );
        debug_assert!(self.p <= c, "p exceeds capacity");
        debug_assert_eq!(
            self.pages.len(),
            self.t1.len() + self.t2.len(),
            "page map and resident lists diverged"
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::MemStore;
    use crate::writeback::NoFailWriteback;

    fn store_with(pages: &[(u64, &[u8])]) -> MemStore {
        let s = MemStore::new();
        for (id, data) in pages {
            s.put(*id, data.to_vec());
        }
        s
    }

    #[test]
    fn ghost_hit_is_not_a_data_hit() {
        let store = store_with(&[(1, b"one"), (2, b"two"), (3, b"three")]);
        let wb = NoFailWriteback;
        let mut c = ArcCache::new(2);
        c.read(1, &store, &wb).unwrap();
        c.read(2, &store, &wb).unwrap();
        // Evict 1 into B1.
        c.read(1, &store, &wb).unwrap(); // 1 -> T2
        c.read(3, &store, &wb).unwrap(); // evicts 2 -> B1 (T1 LRU)
        assert_eq!(c.classify(2), Class::B1);
        assert!(!c.contains_data(2), "ghost entry must not hold data");
        let fetches_before = c.stats().store_fetches;
        let (_, report) = c.read(2, &store, &wb).unwrap();
        assert_eq!(report.outcome, Outcome::GhostHitB1);
        assert!(report.fetched, "ghost hit must fetch content from the store");
        assert_eq!(c.stats().store_fetches, fetches_before + 1);
        assert_eq!(c.stats().hits_t1 + c.stats().hits_t2, 1, "ghost hit is not a hit");
    }

    #[test]
    fn invariants_hold_under_mixed_load() {
        let store = store_with(&[]);
        let wb = NoFailWriteback;
        let mut c = ArcCache::new(3);
        for i in 0..200u64 {
            let id = (i * 7 + i / 3) % 11;
            store.put(id, vec![i as u8]);
            c.read(id, &store, &wb).unwrap();
            if i % 5 == 0 {
                c.write(id, vec![1, 2, 3], &wb).unwrap();
            }
            c.check_invariants();
        }
    }
}
