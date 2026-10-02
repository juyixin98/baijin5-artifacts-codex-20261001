//! MMU：把页表树与 TLB 组合成“翻译 + 权限检查”执行层。
//!
//! 翻译流程（确定性）：
//! 1. 以 `(ASID, VPN)` 查 TLB；命中则用缓存的 PPN/权限，记为 `tlb_hit`；
//! 2. 未命中则走 4 级页表（`walk`），完成后把结果按 4KiB 粒度填入 TLB，记为 `page_walk`；
//! 3. 权限在**最终访问类型**上独立判定，失败返回 [`FaultKind::PermissionDenied`]。
//!
//! 跨页访问（长度使 [start,start+len) 跨越 4KiB 边界）会被拆成多个页内片段，
//! **每一片都独立完成翻译与全部权限检查**——任何一片失败，整次访问按页故障报告，
//! 同时保留已成功片段的中间状态。

use crate::address::{split_across_pages, VirtualAddress};
use crate::config::PAGE_BITS;
use crate::errors::{FaultKind, PageFault, ServiceError};
use crate::pagetable::{PageTableTree, WalkStep};
use crate::pte::{encode_leaf, AccessOp};
use crate::tlb::{Tlb, TlbEntry};
use serde::Serialize;

/// TLB 命中还是走了页表。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TranslateSource {
    TlbHit,
    PageWalk,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Translation {
    pub vaddr: u64,
    pub paddr: u64,
    pub ppn: u64,
    pub op: AccessOp,
    pub leaf_level: u8,
    pub source: TranslateSource,
    pub tlb_filled: bool,
    pub walk_steps: Vec<WalkStep>,
}

/// 翻译失败：故障 + 走到的轨迹（TLB 命中时轨迹为空）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TranslateError {
    pub fault: PageFault,
    pub source: TranslateSource,
    pub walk_steps: Vec<WalkStep>,
}

impl TranslateError {
    fn denied(fault: PageFault, source: TranslateSource, steps: Vec<WalkStep>) -> Self {
        TranslateError {
            fault,
            source,
            walk_steps: steps,
        }
    }
}

/// 翻译单个地址上的单次访问。
pub fn translate(
    tree: &PageTableTree,
    tlb: &mut Tlb,
    asid: u16,
    va: VirtualAddress,
    op: AccessOp,
) -> Result<Translation, TranslateError> {
    let vaddr = va.raw();
    let vpn = vaddr >> PAGE_BITS;

    if let Some(hit) = tlb.lookup(asid, vpn) {
        if !op.permitted_by(&hit.perms) {
            return Err(TranslateError::denied(
                PageFault {
                    kind: FaultKind::PermissionDenied,
                    vaddr,
                    asid,
                    level: hit.leaf_level,
                    index: va.index(hit.leaf_level) as u16,
                    pte: encode_leaf(hit.ppn, &hit.perms),
                    reason: format!(
                        "TLB 缓存的权限 {:?} 不允许 {op:?}（陈旧表项：权限已被页表端降级但未失效）",
                        hit.perms
                    ),
                },
                TranslateSource::TlbHit,
                Vec::new(),
            ));
        }
        let paddr = (hit.ppn << PAGE_BITS) | va.page_offset();
        return Ok(Translation {
            vaddr,
            paddr,
            ppn: hit.ppn,
            op,
            leaf_level: hit.leaf_level,
            source: TranslateSource::TlbHit,
            tlb_filled: false,
            walk_steps: Vec::new(),
        });
    }

    // TLB 未命中：走页表。
    let outcome = tree.walk(asid, va).map_err(|wf| TranslateError {
        fault: wf.fault,
        source: TranslateSource::PageWalk,
        walk_steps: wf.steps,
    })?;
    if !op.permitted_by(&outcome.perms) {
        return Err(TranslateError::denied(
            PageFault {
                kind: FaultKind::PermissionDenied,
                vaddr,
                asid,
                level: outcome.leaf_level,
                index: va.index(outcome.leaf_level) as u16,
                pte: outcome.pte,
                reason: format!(
                    "L{} 叶子权限 {:?} 不允许 {op:?}",
                    outcome.leaf_level, outcome.perms
                ),
            },
            TranslateSource::PageWalk,
            outcome.steps,
        ));
    }
    tlb.insert(TlbEntry {
        asid,
        vpn,
        ppn: outcome.leaf_ppn,
        perms: outcome.perms,
        global: outcome.perms.global,
        leaf_level: outcome.leaf_level,
    });
    Ok(Translation {
        vaddr,
        paddr: outcome.paddr,
        ppn: outcome.leaf_ppn,
        op,
        leaf_level: outcome.leaf_level,
        source: TranslateSource::PageWalk,
        tlb_filled: true,
        walk_steps: outcome.steps,
    })
}

/// 一个页内片段的翻译结果。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "outcome", rename_all = "snake_case")]
pub enum PieceResult {
    Ok {
        vaddr: u64,
        len: u64,
        paddr_start: u64,
        ppn: u64,
        op: AccessOp,
        leaf_level: u8,
        source: TranslateSource,
        tlb_filled: bool,
    },
    Fault {
        vaddr: u64,
        len: u64,
        op: AccessOp,
        kind: String,
        reason: String,
        level: u8,
        pte: u64,
        source: TranslateSource,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AccessReport {
    pub asid: u16,
    pub start_vaddr: u64,
    pub len: u64,
    pub op: AccessOp,
    pub crossed_page: bool,
    pub success: bool,
    pub pieces: Vec<PieceResult>,
    pub walk_steps: Vec<Vec<WalkStep>>,
}

/// 一次可能跨页的访问：逐片翻译并验证全部权限。
pub fn access(
    tree: &PageTableTree,
    tlb: &mut Tlb,
    asid: u16,
    start: VirtualAddress,
    len: u64,
    op: AccessOp,
) -> Result<AccessReport, ServiceError> {
    if len == 0 {
        return Err(ServiceError::Input("访问长度必须 > 0".into()));
    }
    if len > crate::config::MAX_ACCESS_BYTES {
        return Err(ServiceError::Input(format!(
            "单次访问长度 {len} 超过教学模型上限 {} 字节",
            crate::config::MAX_ACCESS_BYTES
        )));
    }
    // 末地址也必须是规范地址（拆分后各片都会校验）。
    let end = start
        .raw()
        .checked_add(len)
        .ok_or_else(|| ServiceError::Input("vaddr + len 溢出 64 位".into()))?;
    let _ = VirtualAddress::new(end - 1)?;

    let pieces_in = split_across_pages(start, len);
    let crossed = pieces_in.len() > 1;
    let mut results = Vec::with_capacity(pieces_in.len());
    let mut all_steps = Vec::new();
    let mut success = true;

    for piece in pieces_in {
        let piece_va = VirtualAddress::new(piece.page_start + piece.offset)?;
        match translate(tree, tlb, asid, piece_va, op) {
            Ok(t) => {
                all_steps.push(t.walk_steps.clone());
                results.push(PieceResult::Ok {
                    vaddr: piece_va.raw(),
                    len: piece.len,
                    paddr_start: t.paddr,
                    ppn: t.ppn,
                    op,
                    leaf_level: t.leaf_level,
                    source: t.source,
                    tlb_filled: t.tlb_filled,
                });
            }
            Err(e) => {
                success = false;
                all_steps.push(e.walk_steps.clone());
                results.push(PieceResult::Fault {
                    vaddr: piece_va.raw(),
                    len: piece.len,
                    op,
                    kind: e.fault.kind.as_str().into(),
                    reason: e.fault.reason.clone(),
                    level: e.fault.level,
                    pte: e.fault.pte,
                    source: e.source,
                });
                // 真实处理器在首个故障片即陷入；保留此前已成功片段的状态后停止。
                break;
            }
        }
    }

    Ok(AccessReport {
        asid,
        start_vaddr: start.raw(),
        len,
        op,
        crossed_page: crossed,
        success,
        pieces: results,
        walk_steps: all_steps,
    })
}
