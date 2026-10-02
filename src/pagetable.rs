//! 多级页表树：4 级遍历、映射建立/拆除、大页-小页覆盖冲突检测。
//!
//! 页表本身存放于“物理帧”中：每张 4KiB 页表占一个 PPN，通过 [`FramePool`] 分配，
//! 但全程只操作本进程内的 HashMap，**绝不触碰真实内核页表**。

use std::collections::HashMap;

use crate::address::{leaf_size, VirtualAddress};
use crate::config::*;
use crate::errors::{FaultKind, PageFault, Result, ServiceError};
use crate::memory::FramePool;
use crate::pte::{self, DecodedPte, Permissions};

/// 一张页表：512 个 64 位 PTE。
#[derive(Debug, Clone)]
pub struct Table {
    pub entries: Box<[u64; ENTRIES_PER_TABLE]>,
}

impl Table {
    fn new() -> Self {
        Table {
            entries: Box::new([0u64; ENTRIES_PER_TABLE]),
        }
    }

    /// 快照恢复用。
    pub fn from_entries(entries: Box<[u64; ENTRIES_PER_TABLE]>) -> Self {
        Table { entries }
    }
}

/// 遍历过程中的单步记录（诊断用：保留关键中间状态）。
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct WalkStep {
    pub level: u8,
    pub index: u16,
    pub table_ppn: u64,
    pub pte: u64,
    pub action: String,
}

/// 成功遍历的结果。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WalkOutcome {
    pub leaf_level: u8,
    pub leaf_ppn: u64,
    pub perms: Permissions,
    pub pte: u64,
    pub paddr: u64,
    pub steps: Vec<WalkStep>,
}

/// 遍历失败：故障 + 已走过的轨迹。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WalkFault {
    pub fault: PageFault,
    pub steps: Vec<WalkStep>,
}

type WalkResult = std::result::Result<WalkOutcome, WalkFault>;

#[derive(Debug, Clone)]
pub struct PageTableTree {
    pub root_ppn: u64,
    pub tables: HashMap<u64, Table>,
}

impl PageTableTree {
    /// 建立一棵只有根表的新树（根表占一个物理帧）。
    pub fn create(pool: &mut FramePool) -> Result<Self> {
        let root_ppn = pool.allocate()?;
        let mut tables = HashMap::new();
        tables.insert(root_ppn, Table::new());
        Ok(PageTableTree { root_ppn, tables })
    }

    /// 树占用的页表帧总数（含根表）。
    pub fn table_frames(&self) -> u64 {
        self.tables.len() as u64
    }

    /// 快照恢复用：直接以给定根与页表集合重建。
    pub fn from_parts(root_ppn: u64, tables: HashMap<u64, Table>) -> Self {
        PageTableTree { root_ppn, tables }
    }

    fn table_at(&self, ppn: u64) -> Result<&Table> {
        self.tables.get(&ppn).ok_or_else(|| {
            ServiceError::Compute(format!(
                "内部不一致：PPN {ppn} 上应存在页表，但帧池中找不到（模拟状态损坏）"
            ))
        })
    }

    /// 从根开始的只读遍历，返回叶子翻译或页故障（含完整轨迹）。
    pub fn walk(&self, asid: u16, va: VirtualAddress) -> WalkResult {
        let vaddr = va.raw();
        let mut table_ppn = self.root_ppn;
        let mut steps = Vec::new();
        for level in 0..LEVELS {
            let index = va.index(level);
            let pte = match self.tables.get(&table_ppn) {
                Some(t) => t.entries[index],
                None => {
                    return Err(WalkFault {
                        fault: PageFault {
                            kind: FaultKind::ReservedFault,
                            vaddr,
                            asid,
                            level,
                            index: index as u16,
                            pte: 0,
                            reason: format!("遍历中途缺少 PPN {table_ppn} 页表（状态损坏）"),
                        },
                        steps,
                    })
                }
            };
            match pte::validate_in_walk(pte, level, vaddr, asid, index) {
                Ok(DecodedPte::Invalid) => {
                    steps.push(WalkStep {
                        level,
                        index: index as u16,
                        table_ppn,
                        pte,
                        action: "invalid".into(),
                    });
                    return Err(WalkFault {
                        fault: PageFault {
                            kind: if level == LEVELS - 1 {
                                FaultKind::PageNotPresent
                            } else {
                                FaultKind::Miss
                            },
                            vaddr,
                            asid,
                            level,
                            index: index as u16,
                            pte,
                            reason: if level == LEVELS - 1 {
                                "末级 PTE V=0：页未映射".to_string()
                            } else {
                                format!("L{level} PTE V=0：下一级页表不存在")
                            },
                        },
                        steps,
                    });
                }
                Ok(DecodedPte::TablePointer { ppn }) => {
                    if level == LEVELS - 1 {
                        steps.push(WalkStep {
                            level,
                            index: index as u16,
                            table_ppn,
                            pte,
                            action: "invalid_leaf_at_final".into(),
                        });
                        return Err(WalkFault {
                            fault: PageFault {
                                kind: FaultKind::PageNotPresent,
                                vaddr,
                                asid,
                                level,
                                index: index as u16,
                                pte,
                                reason: "末级 PTE 为 V=1 但 R/W/X=0：不是合法叶子".into(),
                            },
                            steps,
                        });
                    }
                    steps.push(WalkStep {
                        level,
                        index: index as u16,
                        table_ppn,
                        pte,
                        action: format!("descend->ppn:{ppn:#x}"),
                    });
                    table_ppn = ppn;
                }
                Ok(DecodedPte::Leaf { ppn, perms }) => {
                    if level == 0 {
                        steps.push(WalkStep {
                            level,
                            index: index as u16,
                            table_ppn,
                            pte,
                            action: "illegal_leaf_l0".into(),
                        });
                        return Err(WalkFault {
                            fault: PageFault {
                                kind: FaultKind::ReservedFault,
                                vaddr,
                                asid,
                                level,
                                index: index as u16,
                                pte,
                                reason: "L0(512GiB) 不允许叶子 PTE（超出虚拟地址空间）".into(),
                            },
                            steps,
                        });
                    }
                    let size = leaf_size(level);
                    let paddr = (ppn << PAGE_BITS) | (vaddr & (size - 1));
                    steps.push(WalkStep {
                        level,
                        index: index as u16,
                        table_ppn,
                        pte,
                        action: format!("leaf@L{level}"),
                    });
                    return Ok(WalkOutcome {
                        leaf_level: level,
                        leaf_ppn: ppn,
                        perms,
                        pte,
                        paddr,
                        steps,
                    });
                }
                Err(fault) => {
                    steps.push(WalkStep {
                        level,
                        index: index as u16,
                        table_ppn,
                        pte,
                        action: "reserved_fault".into(),
                    });
                    return Err(WalkFault { fault, steps });
                }
            }
        }
        // 理论不可达：LEVELS 级循环必然在末级返回。
        Err(WalkFault {
            fault: PageFault {
                kind: FaultKind::Miss,
                vaddr,
                asid,
                level: LEVELS - 1,
                index: va.index(LEVELS - 1) as u16,
                pte: 0,
                reason: "遍历异常结束".into(),
            },
            steps,
        })
    }

    /// 建立映射。
    ///
    /// - `leaf_level`：1=1GiB，2=2MiB，3=4KiB；
    /// - 大页与已有映射任何方向的覆盖都以 [`ServiceError::Conflict`] 拒绝。
    pub fn map(
        &mut self,
        pool: &mut FramePool,
        asid: u16,
        va: VirtualAddress,
        leaf_level: u8,
        ppn: u64,
        perms: &Permissions,
    ) -> Result<MapReport> {
        if !(MIN_LEAF_LEVEL..LEVELS).contains(&leaf_level) {
            return Err(ServiceError::Input(format!(
                "叶子层级必须在 {MIN_LEAF_LEVEL}..={}（1=1GiB 2=2MiB 3=4KiB）",
                LEVELS - 1
            )));
        }
        if !perms.read && !perms.write && !perms.execute {
            return Err(ServiceError::Input(
                "叶子 PTE 至少需要 R/W/X 中的一个权限位".into(),
            ));
        }
        if perms.write && !perms.read {
            return Err(ServiceError::Input("非法权限组合 W=1,R=0".into()));
        }
        let size = leaf_size(leaf_level);
        let vaddr = va.raw();
        if vaddr & (size - 1) != 0 {
            return Err(ServiceError::Input(format!(
                "vaddr {vaddr:#x} 未按 {size:#x} 对齐，不能建立 L{leaf_level} 映射"
            )));
        }
        let npages = size >> PAGE_BITS;
        pte::check_ppn(ppn)?;
        if !ppn.is_multiple_of(npages) {
            return Err(ServiceError::Input(format!(
                "物理 PPN {ppn} 未按大页对齐（需 {npages} 帧边界，即 PPN 低位 {} bit 为 0）",
                leaf_ppn_log(npages)
            )));
        }
        if ppn
            .checked_add(npages - 1)
            .is_none_or(|last| last > MAX_PPN)
        {
            return Err(ServiceError::Input(format!(
                "物理范围 PPN {ppn}..{} 超出物理地址空间",
                ppn + npages - 1
            )));
        }

        // Dry-run：统计需新建的页表数并提前发现冲突，保证资源耗尽时不留下半成品。
        let mut needed_tables = 0u64;
        {
            let mut probe_ppn = self.root_ppn;
            for level in 0..leaf_level {
                let index = VirtualAddress::new(vaddr).unwrap().index(level);
                match pte::decode(self.table_at(probe_ppn)?.entries[index]) {
                    DecodedPte::Invalid => {
                        // 从第 level 层起路径全断：level..leaf_level 每层都要新建一张。
                        needed_tables += u64::from(leaf_level) - u64::from(level);
                        break;
                    }
                    DecodedPte::TablePointer { ppn } => probe_ppn = ppn,
                    DecodedPte::Leaf { .. } => {
                        return Err(ServiceError::Conflict(format!(
                            "L{level} 已存在叶子映射，覆盖 vaddr {vaddr:#x} 起始的 {size:#x} 区域：\
                             新映射与其大页范围冲突（大页覆盖已有区域被拒绝，asid={asid}）"
                        )));
                    }
                }
            }
        }
        if pool.available() < needed_tables {
            return Err(ServiceError::Exhausted(format!(
                "建立映射需新建 {needed_tables} 张页表，但帧池仅剩 {} 个空闲帧",
                pool.available()
            )));
        }

        let mut allocated_tables = 0u64;
        let mut table_ppn = self.root_ppn;
        // 逐级前进，缺表则分配；dry-run 已保证帧充足，此处分配不会中途失败。
        for level in 0..leaf_level {
            let index = VirtualAddress::new(vaddr).unwrap().index(level);
            let pte = self.table_at(table_ppn)?.entries[index];
            match pte::decode(pte) {
                DecodedPte::Invalid => {
                    let new_ppn = pool.allocate()?;
                    self.tables.insert(new_ppn, Table::new());
                    let fresh = pte::encode_table_pointer(new_ppn);
                    self.tables.get_mut(&table_ppn).unwrap().entries[index] = fresh;
                    table_ppn = new_ppn;
                    allocated_tables += 1;
                }
                DecodedPte::TablePointer { ppn } => {
                    table_ppn = ppn;
                }
                DecodedPte::Leaf { .. } => {
                    return Err(ServiceError::Conflict(format!(
                        "L{level} 已存在叶子映射，覆盖 vaddr {vaddr:#x} 起始的 {size:#x} 区域：\
                         新映射与其大页范围冲突（大页覆盖已有区域被拒绝，asid={asid}）"
                    )));
                }
            }
        }
        // 到达目标层级：槽位必须为空。
        let index = VirtualAddress::new(vaddr).unwrap().index(leaf_level);
        let existing = self.table_at(table_ppn)?.entries[index];
        if existing & 1 != 0 {
            match pte::decode(existing) {
                DecodedPte::Leaf { .. } => {
                    return Err(ServiceError::Conflict(format!(
                        "vaddr {vaddr:#x} 的 L{leaf_level} 槽位已有叶子映射（PTE={existing:#x}），\
                         重叠/重复映射被拒绝（asid={asid}）"
                    )));
                }
                DecodedPte::TablePointer { .. } => {
                    return Err(ServiceError::Conflict(format!(
                        "vaddr {vaddr:#x} 的 L{leaf_level} 槽位下方已存在小页映射，\
                         新的 {size:#x} 大页将覆盖它们：大小页覆盖冲突被拒绝（asid={asid}）"
                    )));
                }
                DecodedPte::Invalid => unreachable!(),
            }
        }
        let leaf = pte::encode_leaf(ppn, perms);
        self.tables.get_mut(&table_ppn).unwrap().entries[index] = leaf;

        Ok(MapReport {
            leaf_level,
            leaf_pte: leaf,
            leaf_table_ppn: table_ppn,
            index,
            page_tables_allocated: allocated_tables,
            size,
        })
    }

    /// 拆除精确粒度的叶子映射；空中间表自底向上回收（根表保留）。
    pub fn unmap(
        &mut self,
        pool: &mut FramePool,
        asid: u16,
        va: VirtualAddress,
        leaf_level: u8,
    ) -> Result<UnmapReport> {
        if !(MIN_LEAF_LEVEL..LEVELS).contains(&leaf_level) {
            return Err(ServiceError::Input("leaf_level 非法".into()));
        }
        let size = leaf_size(leaf_level);
        let vaddr = va.raw();
        if vaddr & (size - 1) != 0 {
            return Err(ServiceError::Input(format!(
                "vaddr {vaddr:#x} 未按 {size:#x} 对齐",
            )));
        }

        // 记录路径 (table_ppn, index) 以便级联回收。
        let mut path: Vec<(u64, usize)> = Vec::new();
        let mut table_ppn = self.root_ppn;
        for level in 0..leaf_level {
            let index = VirtualAddress::new(vaddr).unwrap().index(level);
            let pte = self.table_at(table_ppn)?.entries[index];
            match pte::decode(pte) {
                DecodedPte::TablePointer { ppn } => {
                    path.push((table_ppn, index));
                    table_ppn = ppn;
                }
                DecodedPte::Leaf { .. } => {
                    return Err(ServiceError::Conflict(format!(
                        "L{level} 存在更大粒度的叶子，无法按 L{leaf_level} 解除映射（asid={asid}）"
                    )));
                }
                DecodedPte::Invalid => {
                    return Err(ServiceError::Conflict(format!(
                        "vaddr {vaddr:#x} 的 L{leaf_level} 映射不存在（路径在 L{level} 中断）"
                    )));
                }
            }
        }
        let index = VirtualAddress::new(vaddr).unwrap().index(leaf_level);
        let slot = self.table_at(table_ppn)?.entries[index];
        match pte::decode(slot) {
            DecodedPte::Leaf { .. } => {}
            DecodedPte::TablePointer { .. } => {
                return Err(ServiceError::Conflict(format!(
                    "L{leaf_level} 槽位不是叶子（下方还有小页映射），请按实际粒度解除（asid={asid}）"
                )));
            }
            DecodedPte::Invalid => {
                return Err(ServiceError::Conflict(format!(
                    "vaddr {vaddr:#x} 的 L{leaf_level} 映射不存在"
                )));
            }
        }
        self.tables.get_mut(&table_ppn).unwrap().entries[index] = 0;

        // 级联回收空页表（从叶子所在表开始，根表不回收）。
        let mut reclaimed_ppns: Vec<u64> = Vec::new();
        let mut child_ppn = table_ppn;
        for (parent_ppn, parent_index) in path.into_iter().rev() {
            let empty = self
                .tables
                .get(&child_ppn)
                .unwrap()
                .entries
                .iter()
                .all(|e| *e == 0);
            if !empty {
                break;
            }
            self.tables.remove(&child_ppn);
            self.tables.get_mut(&parent_ppn).unwrap().entries[parent_index] = 0;
            reclaimed_ppns.push(child_ppn);
            child_ppn = parent_ppn;
        }
        let reclaimed = reclaimed_ppns.len() as u64;
        for ppn in reclaimed_ppns {
            pool.release(ppn)?;
        }

        Ok(UnmapReport {
            cleared_pte: slot,
            reclaimed_tables: reclaimed,
        })
    }

    /// 修改已有叶子的权限（R/W/X/U/G），返回修改前后的 PTE 与覆盖页范围。
    pub fn reprotect(
        &mut self,
        asid: u16,
        va: VirtualAddress,
        new_perms: &Permissions,
    ) -> Result<ReprotectReport> {
        if !new_perms.read && !new_perms.write && !new_perms.execute {
            return Err(ServiceError::Input(
                "叶子 PTE 至少需要 R/W/X 中的一个权限位".into(),
            ));
        }
        if new_perms.write && !new_perms.read {
            return Err(ServiceError::Input("非法权限组合 W=1,R=0".into()));
        }
        let outcome = self.walk(asid, va).map_err(|wf| {
            ServiceError::Conflict(format!(
                "无法修改权限：vaddr {:#x} 处没有有效叶子（{}）",
                va.raw(),
                wf.fault.reason
            ))
        })?;
        let vaddr = va.raw();
        let size = leaf_size(outcome.leaf_level);
        let index = va.index(outcome.leaf_level);
        let leaf_table_ppn = self.table_ppn_of_leaf(va, outcome.leaf_level)?;
        let table = self.tables.get_mut(&leaf_table_ppn).unwrap();
        let old_pte = table.entries[index];
        let new_pte = pte::encode_leaf(outcome.leaf_ppn, new_perms);
        table.entries[index] = new_pte;
        Ok(ReprotectReport {
            old_pte,
            new_pte,
            leaf_level: outcome.leaf_level,
            vpn_start: vaddr >> PAGE_BITS,
            vpn_count: size >> PAGE_BITS,
        })
    }

    /// 重新走一遍以定位叶子所在页表的 PPN（诊断/内部用）。
    fn table_ppn_of_leaf(&self, va: VirtualAddress, leaf_level: u8) -> Result<u64> {
        let mut table_ppn = self.root_ppn;
        for level in 0..leaf_level {
            let index = va.index(level);
            match pte::decode(self.table_at(table_ppn)?.entries[index]) {
                DecodedPte::TablePointer { ppn } => table_ppn = ppn,
                _ => return Err(ServiceError::Compute("重定位叶子页表失败".into())),
            }
        }
        Ok(table_ppn)
    }

    /// 诊断接口：直接改写路径上某级 PTE 的原始值（**不伴随 TLB 失效**）。
    ///
    /// 仅用于构造“TLB 过期”教学夹具：正常的 map/unmap/reprotect 都自动刷 TLB。
    pub fn raw_write_pte(
        &mut self,
        asid: u16,
        va: VirtualAddress,
        level: u8,
        value: u64,
    ) -> Result<RawPteReport> {
        if level >= LEVELS {
            return Err(ServiceError::Input(format!("level 必须在 0..{}", LEVELS)));
        }
        let mut table_ppn = self.root_ppn;
        for l in 0..level {
            let index = va.index(l);
            match pte::decode(self.table_at(table_ppn)?.entries[index]) {
                DecodedPte::TablePointer { ppn } => table_ppn = ppn,
                _ => {
                    return Err(ServiceError::Conflict(format!(
                        "无法到达 L{level}：路径在 L{l} 中断（asid={asid}）"
                    )))
                }
            }
        }
        let index = va.index(level);
        let old = self.table_at(table_ppn)?.entries[index];
        self.tables.get_mut(&table_ppn).unwrap().entries[index] = value;
        Ok(RawPteReport {
            table_ppn,
            index,
            old_pte: old,
            new_pte: value,
        })
    }
}

fn leaf_ppn_log(npages: u64) -> u32 {
    npages.trailing_zeros()
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct MapReport {
    pub leaf_level: u8,
    pub leaf_pte: u64,
    pub leaf_table_ppn: u64,
    pub index: usize,
    pub page_tables_allocated: u64,
    pub size: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct UnmapReport {
    pub cleared_pte: u64,
    pub reclaimed_tables: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct ReprotectReport {
    pub old_pte: u64,
    pub new_pte: u64,
    pub leaf_level: u8,
    pub vpn_start: u64,
    pub vpn_count: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct RawPteReport {
    pub table_ppn: u64,
    pub index: usize,
    pub old_pte: u64,
    pub new_pte: u64,
}
