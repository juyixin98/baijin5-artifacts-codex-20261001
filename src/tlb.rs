//! 受限 TLB 模型。
//!
//! 设计要点（对应行为契约）：
//! - **ASID 隔离**：条目带 ASID；非全局条目只被同 ASID 命中。
//! - **全局位 G**：G=1 的条目任何 ASID 都可命中（失效时必须跨 ASID 清除）。
//! - **显式失效协议**：页表修改方负责调用精确失效（VA 级 / ASID 级 / 全局）；
//!   若修改后“不失效”，旧条目继续命中——这正是“TLB 过期”夹具要观察的现象，
//!   模型不会偷偷帮调用方刷新。
//! - **容量受限 + FIFO 淘汰**：淘汰事件返回给调用方记入诊断日志。
//! - **大页命中优先**：同一 VA 先查 4 MiB 标签，再查 4 KiB 标签。

use crate::config::{BITS_PER_LEVEL, PAGE_BITS};
use crate::types::{AccessKind, PageSize, Permissions, TlbEviction};
use serde::{Deserialize, Serialize};

/// TLB 中缓存的一条翻译。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TlbEntry {
    /// 创建该条目的 ASID（全局条目以此记录来源，但可被任意 ASID 命中）。
    pub asid: u16,
    /// 标签：小页 = VA>>12，大页 = VA>>22。
    pub tag: u64,
    pub page: PageSize,
    /// 缓存的物理基址。
    pub pa_base: u64,
    pub permissions: Permissions,
    pub global: bool,
    /// 填充时的 PTE 原始值快照（过期核对用，不参与普通命中判定）。
    pub pte_snapshot: u64,
    /// 单调递增的装入序号（FIFO）。
    pub seq: u64,
}

impl TlbEntry {
    /// 判断该条目是否可被给定 ASID 的访问命中。
    pub fn matches_asid(&self, asid: u16) -> bool {
        self.global || self.asid == asid
    }

    pub fn allows(&self, kind: AccessKind) -> bool {
        self.permissions.allows(kind)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TlbSnapshot {
    pub capacity: usize,
    pub entries: Vec<TlbEntry>,
    pub fills: u64,
    pub invalidations: u64,
}

#[derive(Debug, Clone)]
pub struct Tlb {
    capacity: usize,
    entries: Vec<TlbEntry>,
    next_seq: u64,
    fills: u64,
    invalidations: u64,
}

impl Tlb {
    pub fn new(capacity: usize) -> Self {
        assert!(capacity >= 1, "TLB 容量必须 >= 1");
        Self {
            capacity,
            entries: Vec::with_capacity(capacity),
            next_seq: 0,
            fills: 0,
            invalidations: 0,
        }
    }

    pub fn capacity(&self) -> usize {
        self.capacity
    }
    pub fn len(&self) -> usize {
        self.entries.len()
    }
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
    pub fn fills(&self) -> u64 {
        self.fills
    }
    pub fn invalidations(&self) -> u64 {
        self.invalidations
    }

    fn small_tag(va: u64) -> u64 {
        va >> PAGE_BITS
    }
    fn large_tag(va: u64) -> u64 {
        va >> (PAGE_BITS + BITS_PER_LEVEL)
    }

    /// 查找：大页优先，再小页；要求 ASID 匹配。返回条目引用。
    pub fn lookup(&self, asid: u16, va: u64) -> Option<&TlbEntry> {
        let ltag = Self::large_tag(va);
        let stag = Self::small_tag(va);
        self.entries
            .iter()
            .find(|e| e.matches_asid(asid) && e.page == PageSize::Large && e.tag == ltag)
            .or_else(|| {
                self.entries
                    .iter()
                    .find(|e| e.matches_asid(asid) && e.page == PageSize::Small && e.tag == stag)
            })
    }

    /// 装入条目；同键旧条目先被精确替换（记一次淘汰，reason=replaced），
    /// 否则容量满时按 FIFO 淘汰最早装入者。返回被淘汰者（若有）。
    pub fn fill(&mut self, mut entry: TlbEntry) -> Option<TlbEviction> {
        let evicted =
            if let Some(pos) = self.entries.iter().position(|e| {
                e.page == entry.page && e.tag == entry.tag && same_key_scope(e, &entry)
            }) {
                let old = self.entries.remove(pos);
                Some(self.make_eviction(old, "replaced"))
            } else if self.entries.len() >= self.capacity {
                let pos = self
                    .entries
                    .iter()
                    .enumerate()
                    .min_by_key(|(_, e)| e.seq)
                    .map(|(i, _)| i)
                    .expect("非空");
                let old = self.entries.remove(pos);
                Some(self.make_eviction(old, "capacity_fifo"))
            } else {
                None
            };
        entry.seq = self.next_seq;
        self.next_seq += 1;
        self.fills += 1;
        self.entries.push(entry);
        evicted
    }

    fn make_eviction(&self, e: TlbEntry, reason: &str) -> TlbEviction {
        TlbEviction {
            asid: e.asid,
            vpn_tag: e.tag,
            page: e.page,
            reason: reason.to_string(),
        }
    }

    /// VA 级精确失效：移除该映射覆盖的条目。
    ///
    /// - 小页失效：移除对应小页条目（同 ASID，或全局时跨全部 ASID）。
    /// - 大页失效：移除大页条目，以及其 4 MiB 覆盖范围内的所有小页条目。
    ///
    /// 返回被移除的条目数。
    pub fn invalidate_va(&mut self, asid: u16, va: u64, page: PageSize, global: bool) -> usize {
        let ltag = Self::large_tag(va);
        let stag_base = ltag << BITS_PER_LEVEL; // 该 4 MiB 区的小页标签下界
        let stag_end = stag_base + (1 << BITS_PER_LEVEL);
        let before = self.entries.len();
        self.entries.retain(|e| {
            let scope = if global { true } else { e.matches_asid(asid) };
            if !scope {
                return true; // 保留：不属于本次失效范围
            }
            match page {
                PageSize::Small => {
                    // 仅失效单个小页；同时防御性移除可能存在的包裹大页。
                    let is_small = e.page == PageSize::Small && e.tag == Self::small_tag(va);
                    let is_covering_large = e.page == PageSize::Large && e.tag == ltag;
                    !(is_small || is_covering_large)
                }
                PageSize::Large => {
                    let is_large = e.page == PageSize::Large && e.tag == ltag;
                    let inside =
                        e.page == PageSize::Small && e.tag >= stag_base && e.tag < stag_end;
                    !(is_large || inside)
                }
            }
        });
        let removed = before - self.entries.len();
        if removed > 0 {
            self.invalidations += 1;
        }
        removed
    }

    /// ASID 级失效：切换/回收 ASID 时清除该地址空间的全部非全局条目。
    pub fn invalidate_asid(&mut self, asid: u16) -> usize {
        let before = self.entries.len();
        self.entries.retain(|e| e.global || e.asid != asid);
        let removed = before - self.entries.len();
        if removed > 0 {
            self.invalidations += 1;
        }
        removed
    }

    /// 全局失效：清除所有 G=1 条目（全局映射变更时必须调用）。
    pub fn invalidate_global(&mut self) -> usize {
        let before = self.entries.len();
        self.entries.retain(|e| !e.global);
        let removed = before - self.entries.len();
        if removed > 0 {
            self.invalidations += 1;
        }
        removed
    }

    pub fn clear(&mut self) -> usize {
        let n = self.entries.len();
        self.entries.clear();
        if n > 0 {
            self.invalidations += 1;
        }
        n
    }

    /// 过期核对（诊断用）：比较缓存快照与当前 PTE 原始值是否一致。
    /// 不影响普通翻译路径。
    pub fn snapshot_differs(&self, asid: u16, va: u64, current_pte: u64) -> bool {
        self.lookup(asid, va)
            .map(|e| e.pte_snapshot != current_pte)
            .unwrap_or(false)
    }

    pub fn snapshot(&self) -> TlbSnapshot {
        TlbSnapshot {
            capacity: self.capacity,
            entries: self.entries.clone(),
            fills: self.fills,
            invalidations: self.invalidations,
        }
    }

    pub fn restore(snap: TlbSnapshot, next_seq: u64) -> Self {
        Self {
            capacity: snap.capacity,
            entries: snap.entries,
            next_seq,
            fills: snap.fills,
            invalidations: snap.invalidations,
        }
    }

    pub fn next_seq(&self) -> u64 {
        self.next_seq
    }
}

/// 两个条目是否属于同一替换键域：全局条目彼此同键；非全局按 ASID。
fn same_key_scope(a: &TlbEntry, b: &TlbEntry) -> bool {
    match (a.global, b.global) {
        (true, true) => true,
        (false, false) => a.asid == b.asid,
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ent(asid: u16, va: u64, page: PageSize, global: bool) -> TlbEntry {
        TlbEntry {
            asid,
            tag: match page {
                PageSize::Small => Tlb::small_tag(va),
                PageSize::Large => Tlb::large_tag(va),
            },
            page,
            pa_base: 0,
            permissions: Permissions::rwx(),
            global,
            pte_snapshot: 0,
            seq: 0,
        }
    }

    #[test]
    fn asid_isolation_blocks_other_address_spaces() {
        let mut tlb = Tlb::new(8);
        tlb.fill(ent(1, 0x1000, PageSize::Small, false));
        assert!(tlb.lookup(1, 0x1000).is_some());
        assert!(tlb.lookup(2, 0x1000).is_none());
    }

    #[test]
    fn global_entries_hit_any_asid() {
        let mut tlb = Tlb::new(8);
        tlb.fill(ent(1, 0x0, PageSize::Large, true));
        assert!(tlb.lookup(2, 0x1234).is_some());
        assert!(tlb.lookup(42, 0x3F_FFFF).is_some());
    }

    #[test]
    fn large_page_lookup_priority() {
        let mut tlb = Tlb::new(8);
        tlb.fill(ent(1, 0x0, PageSize::Large, false));
        tlb.fill(ent(1, 0x2000, PageSize::Small, false));
        let e = tlb.lookup(1, 0x2000).expect("大页应命中");
        assert_eq!(e.page, PageSize::Large);
    }

    #[test]
    fn fifo_eviction_at_capacity() {
        let mut tlb = Tlb::new(2);
        tlb.fill(ent(1, 0x1000, PageSize::Small, false));
        tlb.fill(ent(1, 0x2000, PageSize::Small, false));
        let ev = tlb
            .fill(ent(1, 0x3000, PageSize::Small, false))
            .expect("应淘汰");
        assert_eq!(ev.reason, "capacity_fifo");
        assert_eq!(ev.vpn_tag, Tlb::small_tag(0x1000));
    }

    #[test]
    fn stale_entry_survives_without_invalidation_and_va_invalidate_fixes() {
        let mut tlb = Tlb::new(8);
        let mut writable = ent(1, 0x1000, PageSize::Small, false);
        writable.permissions = Permissions::new(true, true, false);
        tlb.fill(writable);
        // 未失效：旧条目仍可写。
        assert!(tlb.lookup(1, 0x1000).unwrap().allows(AccessKind::Write));
        // 执行协议：VA 级失效后条目消失。
        assert_eq!(tlb.invalidate_va(1, 0x1000, PageSize::Small, false), 1);
        assert!(tlb.lookup(1, 0x1000).is_none());
    }

    #[test]
    fn large_invalidation_removes_contained_small_entries() {
        let mut tlb = Tlb::new(64);
        tlb.fill(ent(1, 0x1000, PageSize::Small, false));
        tlb.fill(ent(1, 0x2000, PageSize::Small, false)); // 同一 4 MiB 区内
        tlb.fill(ent(1, 0x0040_0000, PageSize::Small, false)); // 下一 4 MiB 区起点
        tlb.fill(ent(1, 0x0, PageSize::Large, false));
        let removed = tlb.invalidate_va(1, 0x0, PageSize::Large, false);
        assert_eq!(removed, 3);
        assert!(tlb.lookup(1, 0x0040_0000).is_some());
    }
}
