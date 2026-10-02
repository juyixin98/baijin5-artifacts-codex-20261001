//! 固定机器参数与地址切分。
//!
//! 本服务模拟一台参数固定的教学机器：
//! - 32 位虚拟地址 / 32 位物理地址
//! - 4 KiB 基页，二级页表（类 RISC-V Sv32 切分：10/10/12）
//! - L1 叶子为 4 MiB 大页，L2 叶子为 4 KiB 小页
//!
//! 这些常量属于行为契约的一部分，不允许通过 API 修改；
//! 可配置的只有物理帧池容量、TLB 容量等“教学资源”（见 [`Config`]）。

/// 基页内偏移位数（4 KiB）。
pub const PAGE_BITS: u32 = 12;
/// 虚拟地址位数（固定）。
pub const VA_BITS: u32 = 32;
/// 物理地址位数（固定）。
pub const PA_BITS: u32 = 32;
/// 页表级数（固定二级）。
pub const LEVELS: u8 = 2;
/// 每级页表项索引位数。
pub const BITS_PER_LEVEL: u32 = 10;
/// 每张页表的表项数（2^10 = 1024）。
pub const ENTRIES_PER_TABLE: usize = 1 << BITS_PER_LEVEL;

/// 基页大小 4 KiB。
pub const PAGE_SIZE: u64 = 1 << PAGE_BITS;
/// 大页大小 4 MiB（基页 × 1024）。
pub const LARGE_PAGE_SIZE: u64 = PAGE_SIZE * ENTRIES_PER_TABLE as u64;
/// 基页偏移掩码。
pub const OFFSET_MASK: u64 = PAGE_SIZE - 1;
/// 大页偏移掩码。
pub const LARGE_OFFSET_MASK: u64 = LARGE_PAGE_SIZE - 1;
/// 虚拟地址空间上界（2^32）。
pub const MAX_VIRTUAL: u64 = 1u64 << VA_BITS;
/// 物理地址空间上界（2^32）。
pub const MAX_PHYSICAL: u64 = 1u64 << PA_BITS;
/// 全空间物理帧总数（2^20）。
pub const MAX_FRAMES: u64 = MAX_PHYSICAL / PAGE_SIZE;

// ---- 概念性 PTE 位布局（64 位表项，仅用于教学与保留位校验）----
//  bit 0   V   有效位
//  bit 1   R   可读
//  bit 2   W   可写
//  bit 3   X   可执行
//  bit 4   D   脏位
//  bit 5   G   全局位
//  bit 6   PS  页大小位（L1 上为 1 表示 4 MiB 大页）
//  bit 9:7      保留位（必须为零）
//  bit 31:12    PPN（20 位，对应 32 位物理地址）
//  bit 39:32    保留位（PA 固定 32 位，必须为零）
//  bit 63:40    保留位（必须为零）

pub const PTE_V: u64 = 1 << 0;
pub const PTE_R: u64 = 1 << 1;
pub const PTE_W: u64 = 1 << 2;
pub const PTE_X: u64 = 1 << 3;
pub const PTE_D: u64 = 1 << 4;
pub const PTE_G: u64 = 1 << 5;
pub const PTE_PS: u64 = 1 << 6;
/// 必须为零的保留位集合：bits 9:7、39:32、63:40。
pub const PTE_RESERVED_MASK: u64 = (0b111 << 7) | (0xFF << 32) | (0x00FF_FFFF << 40);
/// PPN 位移。
pub const PTE_PPN_SHIFT: u32 = PAGE_BITS;

/// 一次翻译访问允许跨越的最大长度（一个大页）。
pub const MAX_ACCESS_LEN: u64 = LARGE_PAGE_SIZE;

/// 可配置的教学资源参数。机器位宽等固定常量不在此结构中。
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Config {
    /// 物理帧池帧数（4 KiB/帧）。默认 65536（256 MiB）。
    pub total_frames: u64,
    /// TLB 表项容量。默认 16。
    pub tlb_capacity: usize,
    /// 可分配 ASID 上界（1..=max_asids）。默认 255。
    pub max_asids: u16,
    /// 内存事件环容量。默认 2048。
    pub event_buffer: usize,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            total_frames: 1 << 16,
            tlb_capacity: 16,
            max_asids: u16::MAX - 1,
            event_buffer: 2048,
        }
    }
}

impl Config {
    pub fn validate(&self) -> Result<(), String> {
        if self.total_frames == 0 || self.total_frames > MAX_FRAMES {
            return Err(format!(
                "total_frames 必须在 1..={MAX_FRAMES} 之间（实际 {}）",
                self.total_frames
            ));
        }
        if self.tlb_capacity == 0 {
            return Err("tlb_capacity 必须 >= 1".into());
        }
        if self.max_asids == 0 {
            return Err("max_asids 必须 >= 1".into());
        }
        if self.event_buffer == 0 {
            return Err("event_buffer 必须 >= 1".into());
        }
        Ok(())
    }
}

/// 虚拟地址在某一级的索引（L1 = VPN[1]，L2 = VPN[0]）。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct VpnSplit {
    pub l1: usize,
    pub l2: usize,
    pub offset: u64,
}

/// 按 10/10/12 切分虚拟地址。不做范围校验，调用方需先保证 `va < 2^32`。
pub fn split_vpn(va: u64) -> VpnSplit {
    VpnSplit {
        l1: ((va >> (PAGE_BITS + BITS_PER_LEVEL)) & ((1 << BITS_PER_LEVEL) - 1)) as usize,
        l2: ((va >> PAGE_BITS) & ((1 << BITS_PER_LEVEL) - 1)) as usize,
        offset: va & OFFSET_MASK,
    }
}

/// 判断地址是否落在 32 位虚拟空间内。
pub fn valid_virtual(va: u64) -> bool {
    va < MAX_VIRTUAL
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_known_addresses() {
        // 0x0040_1234 → L1=1, L2=1, offset=0x234
        let s = split_vpn(0x0040_1234);
        assert_eq!(s.l1, 1);
        assert_eq!(s.l2, 1);
        assert_eq!(s.offset, 0x234);

        // 0xFFFF_FFFC → L1=1023, L2=1023, offset=0xFFC
        let s = split_vpn(0xFFFF_FFFC);
        assert_eq!(s.l1, 1023);
        assert_eq!(s.l2, 1023);
        assert_eq!(s.offset, 0xFFC);
    }

    #[test]
    fn fixed_constants_are_contract_values() {
        assert_eq!(PAGE_SIZE, 4096);
        assert_eq!(LARGE_PAGE_SIZE, 4 * 1024 * 1024);
        assert_eq!(VA_BITS, 32);
        assert_eq!(PA_BITS, 32);
        assert_eq!(LEVELS, 2);
        assert_eq!(ENTRIES_PER_TABLE, 1024);
    }

    #[test]
    fn reserved_mask_covers_declared_bits() {
        assert_eq!(
            PTE_RESERVED_MASK & (PTE_V | PTE_R | PTE_W | PTE_X | PTE_D | PTE_G | PTE_PS),
            0
        );
        assert_ne!(PTE_RESERVED_MASK & (1 << 7), 0);
        assert_ne!(PTE_RESERVED_MASK & (1 << 32), 0);
        assert_ne!(PTE_RESERVED_MASK & (1 << 63), 0);
    }
}
