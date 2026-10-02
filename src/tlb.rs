//! TLB：全相联、带 ASID 标签的 LRU 缓存。
//!
//! 缓存粒度为**叶子页**（4KiB），键为 `(ASID, VPN)`。命中时携带叶子 PPN、
//! 权限与所在层级（大页翻译结果在填入时已物化为 4KiB 粒度）。
//!
//! 失效协议（明确、可审计）：
//! - `invalidate_asid(a)`：SFENCE.VMA 语义，刷掉 ASID=a 的所有非全局表项
//!   （`G=1` 的全局项对所有 ASID 可见，不被普通刷除移除）；
//! - `invalidate_page(a, vpn)`：仅刷指定页；
//! - `invalidate_all()`：全刷（含全局项）。
//! - 页表结构发生变化（map/unmap/降级）后，调用方**必须**调用相应失效函数；
//!   模型在每次修改后由 [`crate::pagetable`] 自动执行并记录。

use crate::config::TLB_CAPACITY;
use crate::pte::Permissions;
use std::collections::VecDeque;

/// 4KiB 粒度虚拟页号。
pub type Vpn = u64;

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
pub struct TlbEntry {
    pub asid: u16,
    pub vpn: Vpn,
    pub ppn: u64,
    pub perms: Permissions,
    pub global: bool,
    /// 该翻译来自第几级叶子（1/2/3），诊断用。
    pub leaf_level: u8,
}

#[derive(Debug, Default)]
pub struct Tlb {
    entries: VecDeque<TlbEntry>,
    hits: u64,
    misses: u64,
    invalidations: u64,
    evictions: u64,
}

impl Tlb {
    pub fn new() -> Self {
        Tlb::default()
    }

    /// 全局项在查找时忽略 ASID；非全局项要求 ASID 精确相等（ASID 隔离）。
    pub fn lookup(&mut self, asid: u16, vpn: Vpn) -> Option<TlbEntry> {
        let pos = self
            .entries
            .iter()
            .position(|e| e.vpn == vpn && (e.global || e.asid == asid));
        match pos {
            Some(i) => {
                let e = self.entries.remove(i).unwrap();
                self.entries.push_front(e); // LRU 提升
                self.hits += 1;
                Some(e)
            }
            None => {
                self.misses += 1;
                None
            }
        }
    }

    /// 插入表项；键相同（按查找语义）时覆盖——这是“先刷后写”之外的第二道保险。
    pub fn insert(&mut self, entry: TlbEntry) {
        let existing = self.entries.iter().position(|e| {
            e.vpn == entry.vpn
                && match (e.global, entry.global) {
                    (true, true) => true,
                    (false, false) => e.asid == entry.asid,
                    _ => false,
                }
        });
        if let Some(i) = existing {
            self.entries.remove(i);
        } else if self.entries.len() >= TLB_CAPACITY {
            self.entries.pop_back(); // 淘汰 LRU 尾
            self.evictions += 1;
        }
        self.entries.push_front(entry);
    }

    /// 刷除指定 ASID 的指定页；若存在同名全局项则一并刷除（保守、安全）。
    pub fn invalidate_page(&mut self, asid: u16, vpn: Vpn) -> usize {
        let before = self.entries.len();
        self.entries
            .retain(|e| !(e.vpn == vpn && (e.global || e.asid == asid)));
        let removed = before - self.entries.len();
        if removed > 0 {
            self.invalidations += 1;
        }
        removed
    }

    /// SFENCE.VMA（指定 ASID）：移除该 ASID 的非全局表项，保留全局项。
    pub fn invalidate_asid(&mut self, asid: u16) -> usize {
        let before = self.entries.len();
        self.entries.retain(|e| e.global || e.asid != asid);
        let removed = before - self.entries.len();
        self.invalidations += 1;
        removed
    }

    /// 全刷（含全局项）。
    pub fn invalidate_all(&mut self) -> usize {
        let removed = self.entries.len();
        self.entries.clear();
        if removed > 0 {
            self.invalidations += 1;
        }
        removed
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
    pub fn stats(&self) -> TlbStats {
        TlbStats {
            entries: self.entries.len(),
            capacity: TLB_CAPACITY,
            hits: self.hits,
            misses: self.misses,
            invalidations: self.invalidations,
            evictions: self.evictions,
        }
    }

    pub fn snapshot(&self) -> Vec<TlbEntry> {
        self.entries.iter().copied().collect()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
pub struct TlbStats {
    pub entries: usize,
    pub capacity: usize,
    pub hits: u64,
    pub misses: u64,
    pub invalidations: u64,
    pub evictions: u64,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ent(asid: u16, vpn: Vpn, ppn: u64, global: bool) -> TlbEntry {
        TlbEntry {
            asid,
            vpn,
            ppn,
            perms: Permissions::rwx(),
            global,
            leaf_level: 3,
        }
    }

    #[test]
    fn asid_isolation_holds() {
        let mut tlb = Tlb::new();
        tlb.insert(ent(1, 0x10, 0x100, false));
        tlb.insert(ent(2, 0x10, 0x200, false));
        assert_eq!(tlb.lookup(1, 0x10).unwrap().ppn, 0x100);
        assert_eq!(tlb.lookup(2, 0x10).unwrap().ppn, 0x200);
        assert_eq!(tlb.lookup(3, 0x10), None);
    }

    #[test]
    fn global_entries_visible_across_asids_and_survive_asid_flush() {
        let mut tlb = Tlb::new();
        tlb.insert(ent(1, 0x20, 0x500, true));
        assert_eq!(tlb.lookup(7, 0x20).unwrap().ppn, 0x500);
        assert_eq!(tlb.invalidate_asid(1), 0);
        assert!(tlb.lookup(7, 0x20).is_some());
        assert_eq!(tlb.invalidate_all(), 1);
        assert!(tlb.is_empty());
    }

    #[test]
    fn page_flush_removes_only_target() {
        let mut tlb = Tlb::new();
        tlb.insert(ent(1, 0x30, 1, false));
        tlb.insert(ent(1, 0x31, 2, false));
        assert_eq!(tlb.invalidate_page(1, 0x30), 1);
        assert!(tlb.lookup(1, 0x30).is_none());
        assert!(tlb.lookup(1, 0x31).is_some());
    }

    #[test]
    fn lru_eviction_drops_oldest() {
        let mut tlb = Tlb::new();
        for i in 0..TLB_CAPACITY as u64 {
            tlb.insert(ent(1, i, i, false));
        }
        // 再插入一个，淘汰尾部（vpn=0）。
        tlb.insert(ent(1, 999, 999, false));
        assert!(tlb.lookup(1, 0).is_none());
        assert!(tlb.lookup(1, 1).is_some());
    }
}
