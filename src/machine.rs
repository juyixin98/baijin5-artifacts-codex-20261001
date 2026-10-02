//! 机器编排层：管理多个地址空间（进程）、共享物理帧池、共享 TLB，
//! 并在每次页表变更后强制执行明确的 TLB 失效协议。
//!
//! 失效协议（固定、自动、可审计）：
//! - `map`：成功后按映射覆盖的 4KiB VPN 范围逐页刷除（ASID 局部，含同名全局项）；
//! - `unmap`：同上；
//! - `reprotect`（权限降级/变更）：刷整个受影响叶子的 VPN 范围；
//! - `destroy_process`：SFENCE.VMA（ASID 局部刷除）；
//! - 另提供显式 `sfence_vma` 诊断接口。
//! - `raw_write_pte`（夹具构造用）**故意不刷 TLB**，用于复现陈旧表项。

use std::collections::HashMap;
use std::path::Path;
use std::sync::{Arc, Mutex};

use crate::address::VirtualAddress;
use crate::config::*;
use crate::errors::{Result, ServiceError};
use crate::memory::FramePool;
use crate::mmu::{access, translate, AccessReport, Translation};
use crate::pagetable::{
    MapReport, PageTableTree, RawPteReport, ReprotectReport, UnmapReport, WalkStep,
};
use crate::pte::{AccessOp, Permissions};
use crate::runlog::RunLog;
use crate::tlb::{Tlb, TlbStats};
use serde::{Deserialize, Serialize};

/// 单个地址空间。
#[derive(Debug, Clone)]
pub struct Process {
    pub asid: u16,
    pub name: String,
    pub root_ppn: u64,
    pub table: PageTableTree,
    /// 该空间页表帧（含根表）数量。
    pub table_frames: u64,
}

/// 可持久化的机器快照（采样状态）。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MachineSnapshot {
    pub version: u32,
    pub frame_pool_total: u64,
    pub next_asid: u16,
    pub processes: Vec<ProcessSnapshot>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProcessSnapshot {
    pub asid: u16,
    pub name: String,
    pub root_ppn: u64,
    /// 稀疏页表：ppn -> 512 个 PTE。
    pub tables: HashMap<String, Vec<u64>>,
}

/// 机器信息（诊断）。
#[derive(Debug, Clone, Serialize)]
pub struct MachineInfo {
    pub page_size: u64,
    pub vaddr_bits: u32,
    pub paddr_bits: u32,
    pub levels: u8,
    pub tlb_capacity: usize,
    pub frame_pool_total: u64,
    pub frame_pool_used: u64,
    pub process_count: usize,
    pub max_processes: usize,
    pub tlb: TlbStats,
}

/// 带页表轨迹的叶子信息（诊断）。
#[derive(Debug, Clone, Serialize)]
pub struct LookupInfo {
    pub translation: Translation,
}

#[derive(Debug, Clone, Serialize)]
pub struct WalkInfo {
    pub asid: u16,
    pub vaddr: u64,
    pub steps: Vec<WalkStep>,
}

#[derive(Debug, Clone, Serialize)]
pub struct FlushReport {
    pub scope: String,
    pub asid: Option<u16>,
    pub vpn: Option<u64>,
    pub entries_removed: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct ProcessInfo {
    pub asid: u16,
    pub name: String,
    pub root_ppn: u64,
    pub table_frames: u64,
}

#[derive(Debug)]
struct Inner {
    pool: FramePool,
    tlb: Tlb,
    procs: HashMap<u16, Process>,
    next_asid: u16,
}

#[derive(Debug, Clone)]
pub struct Machine {
    inner: Arc<Mutex<Inner>>,
    log: RunLog,
}

impl Machine {
    pub fn new(frame_pool_total: u64, log: RunLog) -> Self {
        Machine {
            inner: Arc::new(Mutex::new(Inner {
                pool: FramePool::new(frame_pool_total),
                tlb: Tlb::new(),
                procs: HashMap::new(),
                next_asid: 0,
            })),
            log,
        }
    }

    pub fn runlog(&self) -> RunLog {
        self.log.clone()
    }

    pub fn info(&self) -> MachineInfo {
        let g = self.inner.lock().unwrap();
        MachineInfo {
            page_size: PAGE_SIZE,
            vaddr_bits: VADDR_BITS,
            paddr_bits: PADDR_BITS,
            levels: LEVELS,
            tlb_capacity: TLB_CAPACITY,
            frame_pool_total: g.pool.total(),
            frame_pool_used: g.pool.used(),
            process_count: g.procs.len(),
            max_processes: MAX_PROCESSES,
            tlb: g.tlb.stats(),
        }
    }

    pub fn create_process(&self, name: String) -> Result<ProcessInfo> {
        let mut g = self.inner.lock().unwrap();
        if g.procs.len() >= MAX_PROCESSES {
            return Err(ServiceError::Exhausted(format!(
                "地址空间数量达到上限 {MAX_PROCESSES}"
            )));
        }
        if g.next_asid > ASID_MAX {
            return Err(ServiceError::Exhausted(format!(
                "ASID 已耗尽（0..={ASID_MAX}）"
            )));
        }
        let asid = g.next_asid;
        g.next_asid += 1;
        let table = PageTableTree::create(&mut g.pool)?;
        let root_ppn = table.root_ppn;
        let table_frames = table.table_frames();
        g.procs.insert(
            asid,
            Process {
                asid,
                name: name.clone(),
                root_ppn,
                table,
                table_frames,
            },
        );
        Ok(ProcessInfo {
            asid,
            name,
            root_ppn,
            table_frames,
        })
    }

    pub fn list_processes(&self) -> Vec<ProcessInfo> {
        let g = self.inner.lock().unwrap();
        let mut v: Vec<_> = g
            .procs
            .values()
            .map(|p| ProcessInfo {
                asid: p.asid,
                name: p.name.clone(),
                root_ppn: p.root_ppn,
                table_frames: p.table_frames,
            })
            .collect();
        v.sort_by_key(|p| p.asid);
        v
    }

    fn with_proc<R>(&self, asid: u16, f: impl FnOnce(&mut Process) -> Result<R>) -> Result<R> {
        let mut g = self.inner.lock().unwrap();
        let p = g
            .procs
            .get_mut(&asid)
            .ok_or_else(|| ServiceError::Conflict(format!("ASID {asid} 不存在")))?;
        f(p)
    }

    /// 需要同时访问帧池与进程的操作：直接对 Inner 做字段级拆分借用。
    fn with_proc_and_pool<R>(
        &self,
        asid: u16,
        f: impl FnOnce(&mut FramePool, &mut Process) -> Result<R>,
    ) -> Result<R> {
        let mut g = self.inner.lock().unwrap();
        let Inner { pool, procs, .. } = &mut *g;
        let p = procs
            .get_mut(&asid)
            .ok_or_else(|| ServiceError::Conflict(format!("ASID {asid} 不存在")))?;
        f(pool, p)
    }

    pub fn map(
        &self,
        asid: u16,
        vaddr: u64,
        leaf_level: u8,
        ppn: u64,
        perms: Permissions,
    ) -> Result<(MapReport, FlushReport)> {
        let va = VirtualAddress::new(vaddr)?;
        let result = self.with_proc_and_pool(asid, |pool, p| {
            let report = p.table.map(pool, asid, va, leaf_level, ppn, &perms)?;
            p.table_frames = p.table.table_frames();
            Ok(report)
        });
        match result {
            Ok(report) => {
                // 失效协议：覆盖范围逐页刷除。
                let removed = self.flush_vpn_range(asid, vaddr, report.size);
                let flush = FlushReport {
                    scope: "vpn_range".into(),
                    asid: Some(asid),
                    vpn: Some(vaddr >> PAGE_BITS),
                    entries_removed: removed,
                };
                self.log.record(
                    "map",
                    Some(asid),
                    "ok",
                    format!("map L{leaf_level} va={vaddr:#x} ppn={ppn:#x}"),
                    serde_json::to_value(&report).unwrap_or_default(),
                    format!(
                        "映射建立后按 {:#x} 字节 VPN 范围刷除 TLB（移除 {removed} 项）",
                        report.size
                    ),
                );
                Ok((report, flush))
            }
            Err(e) => {
                self.log.record(
                    "map",
                    Some(asid),
                    e.kind(),
                    format!("map 失败 va={vaddr:#x}"),
                    serde_json::json!({"leaf_level": leaf_level, "ppn": ppn}),
                    e.message(),
                );
                Err(e)
            }
        }
    }

    pub fn unmap(
        &self,
        asid: u16,
        vaddr: u64,
        leaf_level: u8,
    ) -> Result<(UnmapReport, FlushReport)> {
        let va = VirtualAddress::new(vaddr)?;
        let result = self.with_proc_and_pool(asid, |pool, p| {
            let report = p.table.unmap(pool, asid, va, leaf_level)?;
            p.table_frames = p.table.table_frames();
            Ok((report, crate::address::leaf_size(leaf_level)))
        });
        match result {
            Ok((report, size)) => {
                let removed = self.flush_vpn_range(asid, vaddr, size);
                let flush = FlushReport {
                    scope: "vpn_range".into(),
                    asid: Some(asid),
                    vpn: Some(vaddr >> PAGE_BITS),
                    entries_removed: removed,
                };
                self.log.record(
                    "unmap",
                    Some(asid),
                    "ok",
                    format!("unmap L{leaf_level} va={vaddr:#x}"),
                    serde_json::to_value(&report).unwrap_or_default(),
                    format!("拆除映射后按 {size:#x} 字节范围刷除 TLB（移除 {removed} 项）"),
                );
                Ok((report, flush))
            }
            Err(e) => {
                self.log.record(
                    "unmap",
                    Some(asid),
                    e.kind(),
                    format!("unmap 失败 va={vaddr:#x}"),
                    serde_json::json!({"leaf_level": leaf_level}),
                    e.message(),
                );
                Err(e)
            }
        }
    }

    pub fn reprotect(
        &self,
        asid: u16,
        vaddr: u64,
        perms: Permissions,
    ) -> Result<(ReprotectReport, FlushReport)> {
        let va = VirtualAddress::new(vaddr)?;
        let result = self.with_proc(asid, |p| p.table.reprotect(asid, va, &perms));
        match result {
            Ok(report) => {
                // 权限可能被降级：整个叶子范围必须失效。
                let removed = self.flush_vpn_range(asid, vaddr, report.vpn_count << PAGE_BITS);
                let flush = FlushReport {
                    scope: "vpn_range".into(),
                    asid: Some(asid),
                    vpn: Some(report.vpn_start),
                    entries_removed: removed,
                };
                self.log.record(
                    "reprotect",
                    Some(asid),
                    "ok",
                    format!(
                        "reprotect va={vaddr:#x} pte {:#x}->{:#x}",
                        report.old_pte, report.new_pte
                    ),
                    serde_json::to_value(&report).unwrap_or_default(),
                    format!(
                        "权限变更后失效叶子覆盖的 {} 个 4KiB 页（移除 {removed} 项），防止陈旧权限",
                        report.vpn_count
                    ),
                );
                Ok((report, flush))
            }
            Err(e) => {
                self.log.record(
                    "reprotect",
                    Some(asid),
                    e.kind(),
                    format!("reprotect 失败 va={vaddr:#x}"),
                    serde_json::json!({}),
                    e.message(),
                );
                Err(e)
            }
        }
    }

    /// 翻译单地址（填 TLB 的常规路径）。
    ///
    /// 返回两类错误：[`MachineError::Input`] 为输入校验失败；
    /// [`MachineError::Fault`] 为页故障（保留故障类别，不 panic）。
    pub fn translate(
        &self,
        asid: u16,
        vaddr: u64,
        op: AccessOp,
    ) -> std::result::Result<Translation, MachineTranslateError> {
        let va = VirtualAddress::new(vaddr).map_err(MachineTranslateError::Input)?;
        let out = {
            let mut g = self.inner.lock().unwrap();
            let Inner { procs, tlb, .. } = &mut *g;
            if !procs.contains_key(&asid) {
                return Err(MachineTranslateError::Input(ServiceError::Conflict(
                    format!("ASID {asid} 不存在"),
                )));
            }
            let p = procs.get(&asid).unwrap();
            translate(&p.table, tlb, asid, va, op)
        };
        match &out {
            Ok(t) => {
                self.log.record(
                    "translate",
                    Some(asid),
                    "ok",
                    format!("translate {op:?} va={vaddr:#x}->pa={:#x}", t.paddr),
                    serde_json::to_value(t).unwrap_or_default(),
                    format!(
                        "来源 {:?}：{}",
                        t.source,
                        if t.source == crate::mmu::TranslateSource::TlbHit {
                            "TLB 命中，直接使用缓存 PPN/权限"
                        } else {
                            "TLB 未命中，完成 4 级遍历并填充 TLB"
                        }
                    ),
                );
            }
            Err(e) => {
                self.log.record(
                    "translate",
                    Some(asid),
                    "page_fault",
                    format!(
                        "translate {op:?} va={vaddr:#x} 故障 {}",
                        e.fault.kind.as_str()
                    ),
                    serde_json::json!({"fault": e.fault, "source": e.source}),
                    e.fault.reason.clone(),
                );
            }
        }
        out.map_err(MachineTranslateError::Fault)
    }

    pub fn access(&self, asid: u16, vaddr: u64, len: u64, op: AccessOp) -> Result<AccessReport> {
        let va = VirtualAddress::new(vaddr)?;
        let report = {
            let mut g = self.inner.lock().unwrap();
            let Inner { procs, tlb, .. } = &mut *g;
            if !procs.contains_key(&asid) {
                return Err(ServiceError::Conflict(format!("ASID {asid} 不存在")));
            }
            let p = procs.get(&asid).unwrap();
            access(&p.table, tlb, asid, va, len, op)
        }?;
        let outcome = if report.success { "ok" } else { "page_fault" };
        self.log.record(
            "access",
            Some(asid),
            outcome,
            format!(
                "access {op:?} va={vaddr:#x} len={len} crossed={}",
                report.crossed_page
            ),
            serde_json::to_value(&report).unwrap_or_default(),
            if report.success {
                "跨页访问的每个片段均独立完成翻译与权限检查".to_string()
            } else {
                "某个片段翻译/权限失败：整次访问按页故障报告（见 pieces 与 walk_steps）".to_string()
            },
        );
        Ok(report)
    }

    /// 原始 PTE 改写（夹具专用）：**不触发 TLB 失效**。
    pub fn raw_write_pte(
        &self,
        asid: u16,
        vaddr: u64,
        level: u8,
        value: u64,
    ) -> Result<RawPteReport> {
        let va = VirtualAddress::new(vaddr)?;
        let report = self.with_proc(asid, |p| p.table.raw_write_pte(asid, va, level, value))?;
        self.log.record(
            "raw_write_pte",
            Some(asid),
            "ok",
            format!("raw_write L{level} va={vaddr:#x} value={value:#x}（不刷 TLB）"),
            serde_json::to_value(&report).unwrap_or_default(),
            "夹具专用：直接改写 PTE 且不执行失效协议，用于构造 TLB 陈旧表项",
        );
        Ok(report)
    }

    pub fn sfence_vma(&self, asid: Option<u16>, vpn: Option<u64>) -> FlushReport {
        let removed = {
            let mut g = self.inner.lock().unwrap();
            match (asid, vpn) {
                (Some(a), Some(v)) => g.tlb.invalidate_page(a, v),
                (Some(a), None) => g.tlb.invalidate_asid(a),
                _ => g.tlb.invalidate_all(),
            }
        };
        let report = FlushReport {
            scope: if vpn.is_some() {
                "page"
            } else if asid.is_some() {
                "asid"
            } else {
                "all"
            }
            .into(),
            asid,
            vpn,
            entries_removed: removed,
        };
        self.log.record(
            "sfence_vma",
            asid,
            "ok",
            format!("sfence_vma asid={asid:?} vpn={vpn:?} removed={removed}"),
            serde_json::to_value(&report).unwrap_or_default(),
            "显式失效：页/ASID/全局三级范围",
        );
        report
    }

    pub fn tlb_snapshot(&self) -> serde_json::Value {
        let g = self.inner.lock().unwrap();
        serde_json::json!({
            "stats": g.tlb.stats(),
            "entries": g.tlb.snapshot(),
        })
    }

    pub fn walk_diagnostic(&self, asid: u16, vaddr: u64) -> Result<WalkInfo> {
        let va = VirtualAddress::new(vaddr)?;
        let g = self.inner.lock().unwrap();
        let p = g
            .procs
            .get(&asid)
            .ok_or_else(|| ServiceError::Conflict(format!("ASID {asid} 不存在")))?;
        match p.table.walk(asid, va) {
            Ok(o) => Ok(WalkInfo {
                asid,
                vaddr,
                steps: o.steps,
            }),
            Err(wf) => {
                let mut steps = wf.steps;
                steps.extend(f_steps(&wf.fault));
                Ok(WalkInfo { asid, vaddr, steps })
            }
        }
    }

    fn flush_vpn_range(&self, asid: u16, vaddr: u64, size: u64) -> usize {
        let count = size >> PAGE_BITS;
        let start_vpn = vaddr >> PAGE_BITS;
        let mut g = self.inner.lock().unwrap();
        let mut removed = 0;
        for i in 0..count {
            removed += g.tlb.invalidate_page(asid, start_vpn + i);
        }
        removed
    }

    pub fn destroy_process(&self, asid: u16) -> Result<FlushReport> {
        let removed = {
            let mut g = self.inner.lock().unwrap();
            if !g.procs.contains_key(&asid) {
                return Err(ServiceError::Conflict(format!("ASID {asid} 不存在")));
            }
            // 逐帧归还该地址空间占用的全部页表帧（含根表）。
            let table_ppns: Vec<u64> = g
                .procs
                .get(&asid)
                .unwrap()
                .table
                .tables
                .keys()
                .copied()
                .collect();
            for ppn in table_ppns {
                g.pool.release(ppn)?;
            }
            g.procs.remove(&asid);
            g.tlb.invalidate_asid(asid)
        };
        self.log.record(
            "destroy_process",
            Some(asid),
            "ok",
            format!("destroy asid={asid}, sfence 移除 {removed} 项"),
            serde_json::json!({"asid": asid}),
            "销毁地址空间并归还其全部页表帧",
        );
        Ok(FlushReport {
            scope: "asid".into(),
            asid: Some(asid),
            vpn: None,
            entries_removed: removed,
        })
    }

    /// 导出机器快照（稀疏页表）。
    pub fn snapshot(&self) -> MachineSnapshot {
        let g = self.inner.lock().unwrap();
        let processes = g
            .procs
            .values()
            .map(|p| ProcessSnapshot {
                asid: p.asid,
                name: p.name.clone(),
                root_ppn: p.root_ppn,
                tables: p
                    .table
                    .tables
                    .iter()
                    .map(|(ppn, t)| (format!("{ppn}"), t.entries.to_vec()))
                    .collect(),
            })
            .collect();
        MachineSnapshot {
            version: 1,
            frame_pool_total: g.pool.total(),
            next_asid: g.next_asid,
            processes,
        }
    }

    pub fn save_snapshot(&self, path: &Path) -> Result<()> {
        let snap = self.snapshot();
        let json = serde_json::to_string_pretty(&snap)
            .map_err(|e| ServiceError::Compute(format!("快照序列化失败: {e}")))?;
        std::fs::write(path, json).map_err(|e| {
            ServiceError::Compute(format!("写入快照文件 {} 失败: {e}", path.display()))
        })?;
        Ok(())
    }

    /// 从快照恢复：重建页表与帧池占用（仅接受本服务版本 1 快照）。
    pub fn restore_snapshot(snap: MachineSnapshot, log: RunLog) -> Result<Self> {
        if snap.version != 1 {
            return Err(ServiceError::Input(format!(
                "快照版本 {} 不受支持（期望 1）",
                snap.version
            )));
        }
        let mut used_frames: std::collections::BTreeSet<u64> = std::collections::BTreeSet::new();
        let mut procs = HashMap::new();
        for ps in &snap.processes {
            let mut tables = HashMap::new();
            for (ppn_s, entries) in &ps.tables {
                let ppn: u64 = ppn_s
                    .parse()
                    .map_err(|_| ServiceError::Input(format!("快照中 PPN '{ppn_s}' 非法")))?;
                if entries.len() != ENTRIES_PER_TABLE {
                    return Err(ServiceError::Input(format!(
                        "快照页表 PPN {ppn} 应有 {ENTRIES_PER_TABLE} 项，实际 {}",
                        entries.len()
                    )));
                }
                let mut arr = Box::new([0u64; ENTRIES_PER_TABLE]);
                arr.copy_from_slice(entries);
                tables.insert(ppn, crate::pagetable::Table::from_entries(arr));
                used_frames.insert(ppn);
            }
            if !tables.contains_key(&ps.root_ppn) {
                return Err(ServiceError::Input(format!(
                    "快照 ASID {} 的根 PPN {} 不在页表集合中",
                    ps.asid, ps.root_ppn
                )));
            }
            let table = PageTableTree::from_parts(ps.root_ppn, tables);
            procs.insert(
                ps.asid,
                Process {
                    asid: ps.asid,
                    name: ps.name.clone(),
                    root_ppn: ps.root_ppn,
                    table,
                    table_frames: ps.tables.len() as u64,
                },
            );
        }
        let max_ppn_plus_one = used_frames.iter().next_back().map(|m| m + 1).unwrap_or(0);
        let mut pool = FramePool::new(snap.frame_pool_total.max(max_ppn_plus_one));
        // 恢复线性分配器水位：把已占用帧视为已分配。
        pool.reserve_existing(used_frames.iter().copied().collect())?;
        Ok(Machine {
            inner: Arc::new(Mutex::new(Inner {
                pool,
                tlb: Tlb::new(),
                procs,
                next_asid: snap.next_asid,
            })),
            log,
        })
    }
}

fn f_steps(f: &crate::errors::PageFault) -> Vec<WalkStep> {
    vec![WalkStep {
        level: f.level,
        index: f.index,
        table_ppn: 0,
        pte: f.pte,
        action: format!("fault:{}", f.kind.as_str()),
    }]
}

/// 翻译路径上的两类错误（页故障必须与输入/服务错误区分）。
#[derive(Debug)]
pub enum MachineTranslateError {
    /// 输入/状态错误（非规范地址、ASID 不存在等）。
    Input(ServiceError),
    /// 页故障（含类别 [`FaultKind`] 与轨迹）。
    Fault(crate::mmu::TranslateError),
}
