//! 页表项（PTE）编解码。
//!
//! 64 位 PTE 布局（教学模型，固定不变）：
//!
//! ```text
//!  63 ............ 54 | 53 ............ 8 | 7 6 |5|4|3|2|1|0|
//!      保留(必须为0)   |        PPN        | RSW|G|U|X|W|R|V|
//! ```
//!
//! - `V=1` 且 `R=W=X=0`：指向下一级页表的指针；
//! - `V=1` 且 `R/W/X` 至少一位置位：叶子 PTE（最终映射）；
//! - `W=1,R=0` 为非法组合（保留故障）；
//! - 大页叶子的 PPN 低位若不为零（物理地址未按大页对齐）→ 保留故障；
//! - bit[63:54] 为保留位，出现即保留故障。

use crate::address::{leaf_ppn_zero_bits, leaf_size};
use crate::config::*;
use crate::errors::{FaultKind, PageFault, ServiceError};
use serde::{Deserialize, Serialize};

pub const BIT_V: u64 = 1 << 0;
pub const BIT_R: u64 = 1 << 1;
pub const BIT_W: u64 = 1 << 2;
pub const BIT_X: u64 = 1 << 3;
pub const BIT_U: u64 = 1 << 4;
pub const BIT_G: u64 = 1 << 5;
pub const RSW_MASK: u64 = 0b11 << 6;
const PPN_LOW_BIT: u32 = 8;
/// PTE 中 PPN 字段的位宽（对应 46 位物理地址；本机再限制为 40 位）。
const PPN_FIELD_BITS: u32 = PADDR_BITS - PAGE_BITS;
const PPN_MASK: u64 = (1u64 << PPN_FIELD_BITS) - 1;
pub const RESERVED_HIGH_MASK: u64 = !0u64 << 54;

/// 叶子页权限。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Permissions {
    pub read: bool,
    pub write: bool,
    pub execute: bool,
    pub user: bool,
    pub global: bool,
}

impl Permissions {
    pub const fn rwx() -> Self {
        Permissions {
            read: true,
            write: true,
            execute: true,
            user: true,
            global: false,
        }
    }
    pub const fn ro() -> Self {
        Permissions {
            read: true,
            write: false,
            execute: true,
            user: true,
            global: false,
        }
    }
}

/// 请求的访问类型。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum AccessOp {
    Read,
    Write,
    Execute,
}

impl AccessOp {
    pub fn permitted_by(self, p: &Permissions) -> bool {
        match self {
            AccessOp::Read => p.read,
            AccessOp::Write => p.write,
            AccessOp::Execute => p.execute,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            AccessOp::Read => "R",
            AccessOp::Write => "W",
            AccessOp::Execute => "X",
        }
    }
}

/// 解码后的 PTE 语义。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DecodedPte {
    Invalid,
    TablePointer { ppn: u64 },
    Leaf { ppn: u64, perms: Permissions },
}

/// 编码一条叶子 PTE（映射建立路径使用；参数已提前校验）。
pub fn encode_leaf(ppn: u64, perms: &Permissions) -> u64 {
    let mut pte = BIT_V
        | (ppn << PPN_LOW_BIT)
        | if perms.read { BIT_R } else { 0 }
        | if perms.write { BIT_W } else { 0 }
        | if perms.execute { BIT_X } else { 0 }
        | if perms.user { BIT_U } else { 0 }
        | if perms.global { BIT_G } else { 0 };
    // RSW 位恒为 0（模型不使用）。
    pte &= !RSW_MASK;
    pte
}

/// 编码页表指针 PTE。
pub fn encode_table_pointer(ppn: u64) -> u64 {
    BIT_V | (ppn << PPN_LOW_BIT)
}

/// 不产生故障的纯解码（用于转储/快照）。
pub fn decode(pte: u64) -> DecodedPte {
    if pte & BIT_V == 0 {
        return DecodedPte::Invalid;
    }
    let ppn = (pte >> PPN_LOW_BIT) & PPN_MASK;
    let perms = Permissions {
        read: pte & BIT_R != 0,
        write: pte & BIT_W != 0,
        execute: pte & BIT_X != 0,
        user: pte & BIT_U != 0,
        global: pte & BIT_G != 0,
    };
    let any_perm = perms.read || perms.write || perms.execute;
    if !any_perm {
        DecodedPte::TablePointer { ppn }
    } else {
        DecodedPte::Leaf { ppn, perms }
    }
}

/// 遍历时对一条 PTE 做完整合法性校验，非法组合转成 [`FaultKind::ReservedFault`]。
pub fn validate_in_walk(
    pte: u64,
    level: u8,
    vaddr: u64,
    asid: u16,
    index: usize,
) -> Result<DecodedPte, PageFault> {
    let mk = |reason: String| PageFault {
        kind: FaultKind::ReservedFault,
        vaddr,
        asid,
        level,
        index: index as u16,
        pte,
        reason,
    };

    if pte & RESERVED_HIGH_MASK != 0 {
        return Err(mk("PTE bit[63:54] 为保留位，必须为 0".into()));
    }
    if pte & BIT_V == 0 {
        return Ok(DecodedPte::Invalid);
    }
    // W=1,R=0 在任何级别都是非法组合。
    if pte & BIT_W != 0 && pte & BIT_R == 0 {
        return Err(mk("非法权限组合 W=1,R=0".into()));
    }
    let ppn = (pte >> PPN_LOW_BIT) & PPN_MASK;
    if ppn > MAX_PPN {
        return Err(mk(format!("PPN {ppn} 超出物理地址空间（<= {MAX_PPN}）")));
    }
    let perms = Permissions {
        read: pte & BIT_R != 0,
        write: pte & BIT_W != 0,
        execute: pte & BIT_X != 0,
        user: pte & BIT_U != 0,
        global: pte & BIT_G != 0,
    };
    let any_perm = perms.read || perms.write || perms.execute;
    if !any_perm {
        // 页表指针 PPN 必须 4KiB 对齐（PPN 字段天然页对齐，只需检查范围）。
        return Ok(DecodedPte::TablePointer { ppn });
    }
    // 叶子：检查大页对齐（低位 PPN 必须为 0）。
    let zero = leaf_ppn_zero_bits(level);
    let low_mask = (1u64 << zero) - 1;
    if ppn & low_mask != 0 {
        return Err(mk(format!(
            "L{level} 大页叶子 PPN 低位 {zero} bit 必须为 0（物理地址未按 {:#x} 对齐）",
            leaf_size(level)
        )));
    }
    Ok(DecodedPte::Leaf { ppn, perms })
}

/// 映射参数校验：PPN 是否落在物理空间内。
pub fn check_ppn(ppn: u64) -> Result<(), ServiceError> {
    if ppn > MAX_PPN {
        return Err(ServiceError::Input(format!(
            "物理页帧号 {ppn} 超出范围 0..={MAX_PPN}"
        )));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encode_decode_roundtrip() {
        let pte = encode_leaf(0xabc, &Permissions::rwx());
        match decode(pte) {
            DecodedPte::Leaf { ppn, perms } => {
                assert_eq!(ppn, 0xabc);
                assert!(perms.read && perms.write && perms.execute);
            }
            other => panic!("期望叶子，得到 {other:?}"),
        }
    }

    #[test]
    fn reserved_w_without_r_is_fault() {
        let pte = BIT_V | BIT_W;
        let err = validate_in_walk(pte, 3, 0, 1, 0).unwrap_err();
        assert_eq!(err.kind, FaultKind::ReservedFault);
    }

    #[test]
    fn reserved_high_bits_are_fault() {
        let pte = BIT_V | BIT_R | (1u64 << 60);
        let err = validate_in_walk(pte, 3, 0, 1, 0).unwrap_err();
        assert_eq!(err.kind, FaultKind::ReservedFault);
    }

    #[test]
    fn misaligned_superpage_ppn_is_reserved_fault() {
        // L1 需要 1GiB 对齐 => PPN 低 18 位为 0；PPN=1 非法。
        let pte = encode_leaf(1, &Permissions::rwx());
        let err = validate_in_walk(pte, 1, 0, 1, 0).unwrap_err();
        assert_eq!(err.kind, FaultKind::ReservedFault);
    }

    #[test]
    fn permission_checks_are_independent() {
        let ro = Permissions::ro();
        assert!(AccessOp::Read.permitted_by(&ro));
        assert!(!AccessOp::Write.permitted_by(&ro));
        assert!(AccessOp::Execute.permitted_by(&ro));
    }
}
