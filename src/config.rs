//! 固定机器参数（教学模型，类 Sv48 的受限 4 级页表）。
//!
//! 这些常量在整个服务中是**固定**的：不支持请求级别的地址位数/页大小配置，
//! 以便夹具中的期望值可以被独立、稳定地复算。

/// 页内偏移位数（4KiB 页）。
pub const PAGE_BITS: u32 = 12;
/// 页大小（字节）。
pub const PAGE_SIZE: u64 = 1 << PAGE_BITS;
/// 页内偏移掩码。
pub const PAGE_OFFSET_MASK: u64 = PAGE_SIZE - 1;

/// 每级页表索引位数。
pub const LEVEL_BITS: u32 = 9;
/// 每个页表项数（512）。
pub const ENTRIES_PER_TABLE: usize = 1 << LEVEL_BITS;
/// 页表总级数：L0 根页表（512GiB 区域）… L3 末级（4KiB 页）。
pub const LEVELS: u8 = 4;
/// 允许出现叶子 PTE 的最高层（L0 不允许为叶子）。
pub const MIN_LEAF_LEVEL: u8 = 1;

/// 虚拟地址位数，bit[63:48] 为保留位，必须为零。
pub const VADDR_BITS: u32 = 48;
/// 合法虚拟地址上界（不含）：2^48。
pub const VADDR_SPACE_SIZE: u64 = 1u64 << VADDR_BITS;
pub const VADDR_MASK: u64 = VADDR_SPACE_SIZE - 1;

/// 物理地址位数。
pub const PADDR_BITS: u32 = 40;
/// 最大物理页帧号（2^28 - 1）。
pub const MAX_PPN: u64 = (1u64 << (PADDR_BITS - PAGE_BITS)) - 1;

/// ASID 为 8 位（0..=255）。
pub const ASID_MAX: u16 = 255;
/// 地址空间数量上限。
pub const MAX_PROCESSES: usize = 64;
/// 单次访存最大字节数（真实加载/存储指令至多跨两个 4KiB 页）。
pub const MAX_ACCESS_BYTES: u64 = 16;

/// TLB 表项数（全相联 LRU）。
pub const TLB_CAPACITY: usize = 16;
/// 内存运行日志环形容量。
pub const RUN_LOG_CAP: usize = 1000;

/// 默认物理帧池容量（帧数；每帧 4KiB）。
pub const DEFAULT_FRAME_POOL: u64 = 4096;
