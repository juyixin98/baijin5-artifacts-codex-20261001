//! 虚拟/物理地址的分解与校验（纯函数，无状态，便于独立测试与夹具复算）。

use crate::config::*;
use crate::errors::ServiceError;

/// 经过校验的虚拟地址。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct VirtualAddress(pub u64);

/// 经过校验的物理地址。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PhysicalAddress(pub u64);

impl VirtualAddress {
    /// 校验规范地址：bit[63:48] 必须全部为零（保留位）。
    pub fn new(raw: u64) -> Result<Self, ServiceError> {
        if raw >= VADDR_SPACE_SIZE {
            return Err(ServiceError::Input(format!(
                "vaddr {raw:#x} 不是规范地址：bit[63:48] 保留位必须为 0（地址空间 0..{VADDR_SPACE_SIZE:#x}）"
            )));
        }
        Ok(VirtualAddress(raw))
    }

    pub fn raw(self) -> u64 {
        self.0
    }

    pub fn page_offset(self) -> u64 {
        self.0 & PAGE_OFFSET_MASK
    }

    /// 取第 `level` 级（0=根, 3=末级）的 9 位索引。
    pub fn index(self, level: u8) -> usize {
        let shift = PAGE_BITS + LEVEL_BITS * (LEVELS as u32 - 1 - level as u32);
        ((self.0 >> shift) & ((1u64 << LEVEL_BITS) - 1)) as usize
    }

    /// 四级索引，从根到叶。
    pub fn indices(self) -> [usize; LEVELS as usize] {
        let mut out = [0usize; LEVELS as usize];
        for (l, slot) in out.iter_mut().enumerate() {
            *slot = self.index(l as u8);
        }
        out
    }

    /// 向下对齐到 `size`（size 必须为 2 的幂且 >= PAGE_SIZE）。
    pub fn align_down(self, size: u64) -> u64 {
        self.0 & !(size - 1)
    }
}

impl PhysicalAddress {
    pub fn new(raw: u64) -> Result<Self, ServiceError> {
        if raw >= 1u64 << PADDR_BITS {
            return Err(ServiceError::Input(format!(
                "paddr {raw:#x} 超出 {PADDR_BITS} 位物理地址空间"
            )));
        }
        Ok(PhysicalAddress(raw))
    }

    pub fn raw(self) -> u64 {
        self.0
    }

    pub fn frame_number(self) -> u64 {
        self.0 >> PAGE_BITS
    }
}

/// 第 `level` 级叶子页的字节大小：L1=1GiB, L2=2MiB, L3=4KiB。
pub fn leaf_size(level: u8) -> u64 {
    assert!((MIN_LEAF_LEVEL..LEVELS).contains(&level));
    let steps = LEVELS as u32 - 1 - level as u32;
    PAGE_SIZE << (LEVEL_BITS * steps)
}

/// 第 `level` 级叶子 PPN 必须为零的低位个数（大页对齐）。
pub fn leaf_ppn_zero_bits(level: u8) -> u32 {
    LEVEL_BITS * (LEVELS as u32 - 1 - level as u32)
}

/// 把一个起始 VA 与长度切分成“同一页内连续”的片段（用于跨页访问拆分）。
///
/// 每片记录：所属页的起始 VA、片内起始偏移、片长度。
/// 调用方随后对**每一片**独立完成遍历与权限检查。
pub fn split_across_pages(start: VirtualAddress, len: u64) -> Vec<PagePiece> {
    let mut pieces = Vec::new();
    let mut remaining = len;
    let mut cur = start.raw();
    while remaining > 0 {
        let offset = cur & PAGE_OFFSET_MASK;
        let chunk = core::cmp::min(PAGE_SIZE - offset, remaining);
        pieces.push(PagePiece {
            page_start: cur & !PAGE_OFFSET_MASK,
            offset,
            len: chunk,
        });
        cur += chunk;
        remaining -= chunk;
    }
    pieces
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PagePiece {
    pub page_start: u64,
    pub offset: u64,
    pub len: u64,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn indices_of_known_vaddr() {
        // 0x0000_0000_1234_5678:
        // L0=0 L1=0 L2=0x91 L3=0x145（独立手算：va>>12=0x12345,
        // &0x1ff=0x145；>>9 再 &0x1ff=0x91）
        let va = VirtualAddress::new(0x1234_5678).unwrap();
        let idx = va.indices();
        assert_eq!(idx, [0, 0, 0x91, 0x145]);
        assert_eq!(va.page_offset(), 0x678);
    }

    #[test]
    fn noncanonical_vaddr_rejected() {
        assert!(VirtualAddress::new(1u64 << 48).is_err());
        assert!(VirtualAddress::new(u64::MAX).is_err());
    }

    #[test]
    fn leaf_sizes_are_1g_2m_4k() {
        assert_eq!(leaf_size(1), 1 << 30);
        assert_eq!(leaf_size(2), 1 << 21);
        assert_eq!(leaf_size(3), 1 << 12);
        assert_eq!(leaf_ppn_zero_bits(1), 18);
        assert_eq!(leaf_ppn_zero_bits(2), 9);
        assert_eq!(leaf_ppn_zero_bits(3), 0);
    }

    #[test]
    fn split_handles_cross_page_write() {
        // 从 0x0ff0 写 32 字节：跨 0x0000 与 0x1000 两页。
        let pieces = split_across_pages(VirtualAddress::new(0x0ff0).unwrap(), 32);
        assert_eq!(pieces.len(), 2);
        assert_eq!(pieces[0].page_start, 0x0000);
        assert_eq!(pieces[0].offset, 0xff0);
        assert_eq!(pieces[0].len, 16);
        assert_eq!(pieces[1].page_start, 0x1000);
        assert_eq!(pieces[1].offset, 0);
        assert_eq!(pieces[1].len, 16);
    }

    #[test]
    fn split_within_one_page() {
        let pieces = split_across_pages(VirtualAddress::new(0x1004).unwrap(), 8);
        assert_eq!(pieces.len(), 1);
        assert_eq!(pieces[0].len, 8);
    }
}
