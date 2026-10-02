//! 统一错误契约。
//!
//! 四类工程错误必须可区分（HTTP 状态码 + 机器可读 `category`/`code`）：
//! - [`DomainError::Input`]：输入错误（400）
//! - [`DomainError::StateConflict`]：状态冲突（409），如大小页覆盖、重复映射
//! - [`DomainError::ResourceExhausted`]：资源耗尽（507），如物理帧 / ASID 用完
//! - [`DomainError::Computation`]：计算失败（422），如地址加法溢出
//!
//! 页故障不是服务端错误：翻译“成功地判定为故障”，返回 200 + `fault` 负载
//! （见 api 层），故障类型由 [`FaultKind`] 区分。

use crate::types::{AccessKind, OverlapInfo, PageSize, WalkStep};
use serde::Serialize;

/// 页故障类型——翻译流程输出故障分类，绝不 panic。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum FaultKind {
    /// 某级 PTE 无效（V=0 或空表项）。
    NotPresent { level: u8, index: usize },
    /// 叶子存在但权限不满足本次访问。
    PermissionDenied {
        need: AccessKind,
        readable: bool,
        writable: bool,
        executable: bool,
    },
    /// PTE 中保留位为 1。
    ReservedBit { pte_addr: u64, pte_raw: u64 },
    /// 页大小位出现在不允许的级别（L2 上 PS=1）。
    MisconfiguredPageSize {
        level: u8,
        pte_addr: u64,
        pte_raw: u64,
    },
    /// 合法 PTE 但编码在该级无意义（如末级出现“指向下级”的分支 PTE）。
    ReservedEncoding {
        level: u8,
        pte_addr: u64,
        pte_raw: u64,
    },
    /// 分支 PTE 指向的页表帧不存在（模型内部记账不一致，正常夹具不应触发）。
    TableMissing { level: u8, table_pa: u64 },
    /// 分支 PTE 的次表物理地址未按 4 KiB 对齐。
    MisalignedNextTable { pte_addr: u64, next_phys: u64 },
    /// 叶子物理基址未按页大小对齐。
    MisalignedLeaf { page: PageSize, phys_base: u64 },
}

impl FaultKind {
    pub fn code(&self) -> &'static str {
        match self {
            FaultKind::NotPresent { .. } => "PAGE_FAULT_NOT_PRESENT",
            FaultKind::PermissionDenied { .. } => "PAGE_FAULT_PERMISSION",
            FaultKind::ReservedBit { .. } => "PAGE_FAULT_RESERVED_BIT",
            FaultKind::MisconfiguredPageSize { .. } => "PAGE_FAULT_BAD_PS",
            FaultKind::ReservedEncoding { .. } => "PAGE_FAULT_RESERVED_ENCODING",
            FaultKind::TableMissing { .. } => "PAGE_FAULT_TABLE_MISSING",
            FaultKind::MisalignedNextTable { .. } => "PAGE_FAULT_BAD_NEXT_ALIGN",
            FaultKind::MisalignedLeaf { .. } => "PAGE_FAULT_BAD_LEAF_ALIGN",
        }
    }

    pub fn explain(&self) -> String {
        match self {
            FaultKind::NotPresent { level, index } => {
                format!("L{level} 索引 {index} 的页表项无效（V=0），映射不存在")
            }
            FaultKind::PermissionDenied { need, .. } => {
                format!("叶子权限不满足 {} 访问", need.as_str())
            }
            FaultKind::ReservedBit { pte_addr, .. } => {
                format!("PTE @ {pte_addr:#010x} 保留位非零")
            }
            FaultKind::MisconfiguredPageSize { level, .. } => {
                format!("L{level} 不允许设置页大小位 PS（仅 L1 可为大页）")
            }
            FaultKind::ReservedEncoding { level, .. } => {
                format!("L{level} 出现该级不支持的 PTE 编码（末级不能是分支项）")
            }
            FaultKind::TableMissing { level, table_pa } => {
                format!("L{level} 分支指向的页表帧 {table_pa:#010x} 不存在")
            }
            FaultKind::MisalignedNextTable { next_phys, .. } => {
                format!("次页表物理地址 {next_phys:#010x} 未按 4 KiB 对齐")
            }
            FaultKind::MisalignedLeaf { page, phys_base } => {
                format!(
                    "{} 叶子物理基址 {phys_base:#010x} 未按 {} 对齐",
                    page.as_str(),
                    page.as_str()
                )
            }
        }
    }
}

/// 一次翻译观察到的页故障（含诊断轨迹）。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct PageFault {
    pub run_id: String,
    pub asid: u16,
    pub va: u64,
    pub access: AccessKind,
    pub kind: FaultKind,
    pub walk: Vec<WalkStep>,
    pub reason: String,
    /// 跨页访问在故障发生前已成功翻译的段（关键中间状态，用于重放）。
    pub completed_segments: Vec<crate::types::Segment>,
    /// 故障段的翻译来源：tlb 或 walk。
    pub fault_source: String,
}

/// 状态冲突细分。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ConflictKind {
    /// 目标虚拟页已有不同映射（取消映射后才能重建）。
    MappingExists { va: u64, page: PageSize },
    /// 大页与已有小页（或反向）覆盖范围重叠。
    LargeSmallOverlap(OverlapInfo),
    /// ASID 尚未创建。
    UnknownAsid { asid: u16 },
    /// 请求的物理区间已被其他映射占用（物理别名不被允许）。
    PhysicalRangeOccupied { pa: u64, frames: u64 },
}

/// 资源耗尽细分。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ResourceKind {
    /// 物理帧不足。
    Frames { requested: u64, available: u64 },
    /// ASID 编号空间耗尽。
    Asids { max: u16 },
}

/// 域错误（非 panic 的全部失败路径）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DomainError {
    /// 400 输入错误。
    Input(String),
    /// 409 状态冲突。
    StateConflict(ConflictKind),
    /// 507 资源耗尽。
    ResourceExhausted(ResourceKind),
    /// 422 计算失败（溢出等）。
    Computation(String),
    /// 404 资源不存在。
    NotFound(String),
}

impl DomainError {
    pub fn category(&self) -> &'static str {
        match self {
            DomainError::Input(_) => "input_error",
            DomainError::StateConflict(_) => "state_conflict",
            DomainError::ResourceExhausted(_) => "resource_exhausted",
            DomainError::Computation(_) => "computation_failure",
            DomainError::NotFound(_) => "not_found",
        }
    }

    pub fn code(&self) -> &'static str {
        match self {
            DomainError::Input(_) => "INVALID_REQUEST",
            DomainError::StateConflict(c) => match c {
                ConflictKind::MappingExists { .. } => "MAPPING_EXISTS",
                ConflictKind::LargeSmallOverlap(_) => "LARGE_SMALL_OVERLAP",
                ConflictKind::UnknownAsid { .. } => "UNKNOWN_ASID",
                ConflictKind::PhysicalRangeOccupied { .. } => "PHYSICAL_RANGE_OCCUPIED",
            },
            DomainError::ResourceExhausted(r) => match r {
                ResourceKind::Frames { .. } => "OUT_OF_FRAMES",
                ResourceKind::Asids { .. } => "OUT_OF_ASIDS",
            },
            DomainError::Computation(_) => "COMPUTATION_FAILED",
            DomainError::NotFound(_) => "NOT_FOUND",
        }
    }

    pub fn http_status(&self) -> u16 {
        match self {
            DomainError::Input(_) => 400,
            DomainError::StateConflict(_) => 409,
            DomainError::ResourceExhausted(_) => 507,
            DomainError::Computation(_) => 422,
            DomainError::NotFound(_) => 404,
        }
    }

    pub fn detail(&self) -> String {
        match self {
            DomainError::Input(s) => s.clone(),
            DomainError::StateConflict(c) => match c {
                ConflictKind::MappingExists { va, page } => {
                    format!("VA {va:#010x}（{} 页）上已存在映射", page.as_str())
                }
                ConflictKind::LargeSmallOverlap(o) => format!(
                    "{} 映射 VA {:#010x} 与已有 {} 映射 VA {:#010x} 覆盖范围重叠",
                    o.requested_page.as_str(),
                    o.requested_va,
                    o.existing_page.as_str(),
                    o.existing_va
                ),
                ConflictKind::UnknownAsid { asid } => format!("ASID {asid} 尚未创建"),
                ConflictKind::PhysicalRangeOccupied { pa, frames } => {
                    format!(
                        "物理区间 PA {pa:#010x} 起 {} 帧已被占用（不允许物理别名）",
                        frames
                    )
                }
            },
            DomainError::ResourceExhausted(r) => match r {
                ResourceKind::Frames {
                    requested,
                    available,
                } => {
                    format!("物理帧不足：需要 {requested}，空闲 {available}")
                }
                ResourceKind::Asids { max } => format!("ASID 编号空间耗尽（上界 {max}）"),
            },
            DomainError::Computation(s) => s.clone(),
            DomainError::NotFound(s) => s.clone(),
        }
    }
}

impl std::fmt::Display for DomainError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "[{}] {}", self.code(), self.detail())
    }
}

impl std::error::Error for DomainError {}

pub type DomainResult<T> = Result<T, DomainError>;

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::Permissions;

    #[test]
    fn every_fault_kind_has_stable_code_and_nonempty_explain() {
        let cases = [
            FaultKind::NotPresent { level: 2, index: 7 },
            FaultKind::PermissionDenied {
                need: AccessKind::Write,
                readable: true,
                writable: false,
                executable: true,
            },
            FaultKind::ReservedBit {
                pte_addr: 0x1000,
                pte_raw: 1 << 7,
            },
            FaultKind::MisconfiguredPageSize {
                level: 2,
                pte_addr: 0,
                pte_raw: 0,
            },
            FaultKind::ReservedEncoding {
                level: 2,
                pte_addr: 0,
                pte_raw: 0,
            },
            FaultKind::MisalignedNextTable {
                pte_addr: 0,
                next_phys: 0x10,
            },
            FaultKind::MisalignedLeaf {
                page: PageSize::Large,
                phys_base: 0x1000,
            },
            FaultKind::TableMissing {
                level: 2,
                table_pa: 0x4000,
            },
        ];
        for f in cases {
            assert!(!f.code().is_empty());
            assert!(!f.explain().is_empty());
        }
    }

    #[test]
    fn domain_error_categories_and_status_codes_are_distinct() {
        assert_eq!(DomainError::Input("x".into()).http_status(), 400);
        assert_eq!(
            DomainError::StateConflict(ConflictKind::UnknownAsid { asid: 9 }).http_status(),
            409
        );
        assert_eq!(
            DomainError::ResourceExhausted(ResourceKind::Asids { max: 3 }).http_status(),
            507
        );
        assert_eq!(DomainError::Computation("ovf".into()).http_status(), 422);
        assert_eq!(DomainError::NotFound("n".into()).http_status(), 404);
        assert_eq!(
            DomainError::StateConflict(ConflictKind::PhysicalRangeOccupied { pa: 0, frames: 2 })
                .code(),
            "PHYSICAL_RANGE_OCCUPIED"
        );
        // Display 包含 code。
        let msg = DomainError::Input("bad".into()).to_string();
        assert!(msg.contains("INVALID_REQUEST") && msg.contains("bad"));
        let _ = Permissions::none();
    }
}
