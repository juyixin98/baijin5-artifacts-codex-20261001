//! 页表存储结构与纯走表逻辑（不直接依赖 TLB / 帧池 / 网络）。
//!
//! 物理内存中只有“页表帧”需要保存表项内容；叶子数据帧只做分配记账，
//! 不模拟数据内容（本服务教学的是地址翻译，不是访存）。

use crate::config::{split_vpn, ENTRIES_PER_TABLE};
use crate::error::FaultKind;
use crate::types::{PageSize, Permissions, Pte, WalkStep};
use serde::{Deserialize, Serialize};

/// 以物理基址索引的页表帧集合。每帧 1024 个 64 位 PTE。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PageTableMemory {
    /// 为了 JSON 可读、可 diff，键用物理地址的十六进制字符串。
    tables: std::collections::BTreeMap<String, Vec<u64>>,
}

impl PageTableMemory {
    pub fn new() -> Self {
        Self {
            tables: std::collections::BTreeMap::new(),
        }
    }

    fn key(pa: u64) -> String {
        format!("{pa:#010x}")
    }

    /// 分配一张清零页表（物理帧由调用方在帧池中记账）。
    pub fn create_table(&mut self, pa: u64) {
        self.tables
            .insert(Self::key(pa), vec![0u64; ENTRIES_PER_TABLE]);
    }

    pub fn contains_table(&self, pa: u64) -> bool {
        self.tables.contains_key(&Self::key(pa))
    }

    pub fn read(&self, table_pa: u64, index: usize) -> Option<u64> {
        self.tables
            .get(&Self::key(table_pa))
            .and_then(|t| t.get(index).copied())
    }

    pub fn write(&mut self, table_pa: u64, index: usize, pte: u64) -> Result<(), String> {
        let t = self
            .tables
            .get_mut(&Self::key(table_pa))
            .ok_or_else(|| format!("内部错误：页表帧 {table_pa:#x} 不存在"))?;
        *t.get_mut(index)
            .ok_or_else(|| format!("内部错误：页表索引 {index} 越界"))? = pte;
        Ok(())
    }

    pub fn destroy_table(&mut self, pa: u64) {
        self.tables.remove(&Self::key(pa));
    }

    pub fn table_count(&self) -> usize {
        self.tables.len()
    }

    pub fn table_keys_hex(&self) -> Vec<String> {
        self.tables.keys().cloned().collect()
    }
}

impl Default for PageTableMemory {
    fn default() -> Self {
        Self::new()
    }
}

/// 走表成功的叶子。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LeafHit {
    pub phys_base: u64,
    pub perms: Permissions,
    pub page: PageSize,
    pub global: bool,
    pub dirty: bool,
    pub pte_addr: u64,
    pub pte_raw: u64,
    pub levels: Vec<String>,
    pub steps: Vec<WalkStep>,
}

/// 走表结果：成功叶子或带类型的页故障。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WalkOutcome {
    Leaf(LeafHit),
    Fault(FaultKind, Vec<WalkStep>),
}

impl WalkOutcome {
    pub fn is_fault(&self) -> bool {
        matches!(self, WalkOutcome::Fault(..))
    }
}

fn step(level: &str, index: usize, pte_addr: u64, raw: u64, note: impl Into<String>) -> WalkStep {
    WalkStep {
        level: level.to_string(),
        index,
        pte_addr,
        pte_raw: raw,
        note: note.into(),
    }
}

/// 纯走表：从 `root_pa` 出发按 10/10/12 查 `va`。
///
/// 故障判定顺序（教学约定，记录在 README）：
/// V=0 → 保留位 → 级别/编码合法性 → 对齐 → 叶子权限（权限在 translate 层按访问类型判）。
/// 本函数只负责定位叶子并做结构性校验；叶子 R/W/X 是否满足具体访问由调用方判定。
pub fn walk(mem: &PageTableMemory, root_pa: u64, va: u64) -> WalkOutcome {
    let split = split_vpn(va);
    let mut steps = Vec::new();

    // ---------- L1 ----------
    let l1_addr = root_pa + (split.l1 as u64) * 8;
    let l1_raw = match mem.read(root_pa, split.l1) {
        Some(r) => r,
        None => {
            return WalkOutcome::Fault(
                FaultKind::TableMissing {
                    level: 1,
                    table_pa: root_pa,
                },
                vec![step(
                    "L1",
                    split.l1,
                    l1_addr,
                    0,
                    "根页表帧缺失（内部状态不一致）",
                )],
            )
        }
    };
    let l1 = Pte::from_raw(l1_raw);
    if !l1.valid() {
        steps.push(step("L1", split.l1, l1_addr, l1_raw, "V=0，映射不存在"));
        return WalkOutcome::Fault(
            FaultKind::NotPresent {
                level: 1,
                index: split.l1,
            },
            steps,
        );
    }
    if l1.has_reserved_bits() {
        steps.push(step("L1", split.l1, l1_addr, l1_raw, "保留位非零"));
        return WalkOutcome::Fault(
            FaultKind::ReservedBit {
                pte_addr: l1_addr,
                pte_raw: l1_raw,
            },
            steps,
        );
    }

    if l1.is_leaf() {
        if !l1.large() {
            steps.push(step(
                "L1",
                split.l1,
                l1_addr,
                l1_raw,
                "L1 叶子必须置 PS=1（4 MiB 大页），PS=0 的叶子是保留编码",
            ));
            return WalkOutcome::Fault(
                FaultKind::ReservedEncoding {
                    level: 1,
                    pte_addr: l1_addr,
                    pte_raw: l1_raw,
                },
                steps,
            );
        }
        let phys = l1.phys_base();
        if !phys.is_multiple_of(crate::config::LARGE_PAGE_SIZE) {
            steps.push(step(
                "L1",
                split.l1,
                l1_addr,
                l1_raw,
                "大页 PPN[0] 非零，物理基址未按 4 MiB 对齐",
            ));
            return WalkOutcome::Fault(
                FaultKind::MisalignedLeaf {
                    page: PageSize::Large,
                    phys_base: phys,
                },
                steps,
            );
        }
        steps.push(step(
            "L1",
            split.l1,
            l1_addr,
            l1_raw,
            format!("大页叶子命中，物理基址 {phys:#010x}"),
        ));
        return WalkOutcome::Leaf(LeafHit {
            phys_base: phys,
            perms: l1.permissions(),
            page: PageSize::Large,
            global: l1.global(),
            dirty: l1.dirty(),
            pte_addr: l1_addr,
            pte_raw: l1_raw,
            levels: vec!["L1".into()],
            steps,
        });
    }

    // L1 分支：R/W/X=0。PS 必须为 0。
    if l1.large() {
        steps.push(step(
            "L1",
            split.l1,
            l1_addr,
            l1_raw,
            "分支 PTE 不允许置 PS=1",
        ));
        return WalkOutcome::Fault(
            FaultKind::MisconfiguredPageSize {
                level: 1,
                pte_addr: l1_addr,
                pte_raw: l1_raw,
            },
            steps,
        );
    }
    let l2_pa = l1.phys_base();
    if !l2_pa.is_multiple_of(crate::config::PAGE_SIZE) {
        steps.push(step(
            "L1",
            split.l1,
            l1_addr,
            l1_raw,
            "次页表地址未按 4 KiB 对齐",
        ));
        return WalkOutcome::Fault(
            FaultKind::MisalignedNextTable {
                pte_addr: l1_addr,
                next_phys: l2_pa,
            },
            steps,
        );
    }
    steps.push(step(
        "L1",
        split.l1,
        l1_addr,
        l1_raw,
        format!("分支命中，L2 页表 @ {l2_pa:#010x}"),
    ));

    // ---------- L2 ----------
    let l2_addr = l2_pa + (split.l2 as u64) * 8;
    let l2_raw = match mem.read(l2_pa, split.l2) {
        Some(r) => r,
        None => {
            steps.push(step(
                "L2",
                split.l2,
                l2_addr,
                0,
                "L2 页表帧缺失（内部状态不一致）",
            ));
            return WalkOutcome::Fault(
                FaultKind::TableMissing {
                    level: 2,
                    table_pa: l2_pa,
                },
                steps,
            );
        }
    };
    let l2 = Pte::from_raw(l2_raw);
    if !l2.valid() {
        steps.push(step("L2", split.l2, l2_addr, l2_raw, "V=0，基页映射不存在"));
        return WalkOutcome::Fault(
            FaultKind::NotPresent {
                level: 2,
                index: split.l2,
            },
            steps,
        );
    }
    if l2.has_reserved_bits() {
        steps.push(step("L2", split.l2, l2_addr, l2_raw, "保留位非零"));
        return WalkOutcome::Fault(
            FaultKind::ReservedBit {
                pte_addr: l2_addr,
                pte_raw: l2_raw,
            },
            steps,
        );
    }
    if l2.large() {
        steps.push(step(
            "L2",
            split.l2,
            l2_addr,
            l2_raw,
            "末级 PTE 不允许置 PS=1",
        ));
        return WalkOutcome::Fault(
            FaultKind::MisconfiguredPageSize {
                level: 2,
                pte_addr: l2_addr,
                pte_raw: l2_raw,
            },
            steps,
        );
    }
    if !l2.is_leaf() {
        steps.push(step(
            "L2",
            split.l2,
            l2_addr,
            l2_raw,
            "末级不能再指向下一级页表",
        ));
        return WalkOutcome::Fault(
            FaultKind::ReservedEncoding {
                level: 2,
                pte_addr: l2_addr,
                pte_raw: l2_raw,
            },
            steps,
        );
    }
    let phys = l2.phys_base();
    steps.push(step(
        "L2",
        split.l2,
        l2_addr,
        l2_raw,
        format!("基页叶子命中，物理基址 {phys:#010x}"),
    ));
    WalkOutcome::Leaf(LeafHit {
        phys_base: phys,
        perms: l2.permissions(),
        page: PageSize::Small,
        global: l2.global(),
        dirty: l2.dirty(),
        pte_addr: l2_addr,
        pte_raw: l2_raw,
        levels: vec!["L1".into(), "L2".into()],
        steps,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::{LARGE_PAGE_SIZE, PAGE_SIZE};

    fn l1_with(idx: usize, raw: u64) -> (PageTableMemory, u64) {
        let mut mem = PageTableMemory::new();
        mem.create_table(0);
        mem.write(0, idx, raw).unwrap();
        (mem, 0)
    }

    #[test]
    fn empty_root_gives_not_present_l1() {
        let mut mem = PageTableMemory::new();
        mem.create_table(0);
        match walk(&mem, 0, 0x1234) {
            WalkOutcome::Fault(FaultKind::NotPresent { level, .. }, _) => assert_eq!(level, 1),
            other => panic!("期望 L1 not-present，实际 {other:?}"),
        }
    }

    #[test]
    fn large_leaf_translates() {
        let phys = 0x0040_0000u64;
        let pte = Pte::leaf(phys, Permissions::rwx(), true, false, false).raw;
        let (mem, root) = l1_with(1, pte); // L1 索引 1
        let va = 0x0040_1234;
        match walk(&mem, root, va) {
            WalkOutcome::Leaf(h) => {
                assert_eq!(h.page, PageSize::Large);
                assert_eq!(h.phys_base + (va % LARGE_PAGE_SIZE), phys + 0x1234);
            }
            other => panic!("期望大页命中，实际 {other:?}"),
        }
    }

    #[test]
    fn small_leaf_translates_two_levels() {
        let mut mem = PageTableMemory::new();
        mem.create_table(0);
        mem.create_table(PAGE_SIZE); // L2 表放在物理帧 1
        mem.write(0, 1, Pte::branch(PAGE_SIZE).raw).unwrap();
        let data_pa = 0x10_0000;
        mem.write(
            PAGE_SIZE,
            1,
            Pte::leaf(data_pa, Permissions::ro(), false, false, false).raw,
        )
        .unwrap();
        match walk(&mem, 0, 0x0040_1abc) {
            WalkOutcome::Leaf(h) => {
                assert_eq!(h.page, PageSize::Small);
                assert_eq!(h.phys_base, data_pa);
                assert_eq!(h.levels, vec!["L1", "L2"]);
            }
            other => panic!("期望小页命中，实际 {other:?}"),
        }
    }

    #[test]
    fn reserved_bits_are_detected() {
        let (mem, root) = l1_with(
            0,
            Pte::leaf(0, Permissions::rwx(), true, false, false).raw | (1 << 7),
        );
        match walk(&mem, root, 0) {
            WalkOutcome::Fault(FaultKind::ReservedBit { .. }, _) => {}
            other => panic!("期望保留位故障，实际 {other:?}"),
        }
    }

    #[test]
    fn ps_on_l2_is_fault() {
        let mut mem = PageTableMemory::new();
        mem.create_table(0);
        mem.create_table(PAGE_SIZE);
        mem.write(0, 0, Pte::branch(PAGE_SIZE).raw).unwrap();
        mem.write(
            PAGE_SIZE,
            0,
            Pte::leaf(0, Permissions::rwx(), false, false, false).raw | (1 << 6),
        )
        .unwrap();
        match walk(&mem, 0, 0) {
            WalkOutcome::Fault(FaultKind::MisconfiguredPageSize { level, .. }, _) => {
                assert_eq!(level, 2)
            }
            other => panic!("期望 L2 PS 故障，实际 {other:?}"),
        }
    }

    #[test]
    fn l1_leaf_without_ps_is_reserved_encoding() {
        // L1 上 R/W/X≠0 但 PS=0。
        let pte = Pte::leaf(0, Permissions::rwx(), false, false, false).raw;
        let (mem, root) = l1_with(0, pte);
        match walk(&mem, root, 0) {
            WalkOutcome::Fault(FaultKind::ReservedEncoding { level, .. }, _) => {
                assert_eq!(level, 1)
            }
            other => panic!("期望 L1 保留编码故障，实际 {other:?}"),
        }
    }

    #[test]
    fn l1_branch_with_ps_is_misconfigured() {
        // 分支（R/W/X=0）却置 PS=1。
        let pte = Pte::branch(PAGE_SIZE).raw | (1 << 6);
        let (mem, root) = l1_with(0, pte);
        match walk(&mem, root, 0) {
            WalkOutcome::Fault(FaultKind::MisconfiguredPageSize { level, .. }, _) => {
                assert_eq!(level, 1)
            }
            other => panic!("期望 L1 PS 误置故障，实际 {other:?}"),
        }
    }
    #[test]
    fn misaligned_large_leaf_is_fault() {
        // 大页 PPN[0] 非零：物理基址未按 4 MiB 对齐（保留 PPN 低位手工构造）。
        let mut pte = Pte::leaf(0, Permissions::rwx(), true, false, false).raw;
        pte |= 1 << crate::config::PTE_PPN_SHIFT; // PPN 最低位
        let (mem, root) = l1_with(0, pte);
        match walk(&mem, root, 0) {
            WalkOutcome::Fault(FaultKind::MisalignedLeaf { page, .. }, _) => {
                assert_eq!(page, PageSize::Large)
            }
            other => panic!("期望大页对齐故障，实际 {other:?}"),
        }
    }

    #[test]
    fn l2_not_present_and_reserved_bit() {
        let mut mem = PageTableMemory::new();
        mem.create_table(0);
        mem.create_table(PAGE_SIZE);
        mem.write(0, 0, Pte::branch(PAGE_SIZE).raw).unwrap();
        // L2 槽保持 0：not-present。
        match walk(&mem, 0, 0) {
            WalkOutcome::Fault(FaultKind::NotPresent { level, index }, _) => {
                assert_eq!(level, 2);
                assert_eq!(index, 0);
            }
            other => panic!("期望 L2 not-present，实际 {other:?}"),
        }
        // 写入带保留位的 L2 叶子。
        mem.write(
            PAGE_SIZE,
            0,
            Pte::leaf(0, Permissions::rwx(), false, false, false).raw | (1 << 8),
        )
        .unwrap();
        match walk(&mem, 0, 0) {
            WalkOutcome::Fault(FaultKind::ReservedBit { .. }, _) => {}
            other => panic!("期望 L2 保留位故障，实际 {other:?}"),
        }
    }

    #[test]
    fn l2_branch_encoding_is_reserved() {
        // L2 槽为“继续分支”的 V=1/R=W=X=0 项。
        let mut mem = PageTableMemory::new();
        mem.create_table(0);
        mem.create_table(PAGE_SIZE);
        mem.write(0, 0, Pte::branch(PAGE_SIZE).raw).unwrap();
        mem.write(PAGE_SIZE, 0, Pte::branch(PAGE_SIZE * 2).raw)
            .unwrap();
        match walk(&mem, 0, 0) {
            WalkOutcome::Fault(FaultKind::ReservedEncoding { level, .. }, _) => {
                assert_eq!(level, 2)
            }
            other => panic!("期望 L2 保留编码故障，实际 {other:?}"),
        }
    }
}
