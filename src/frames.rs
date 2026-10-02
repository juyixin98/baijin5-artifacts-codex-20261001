//! 物理帧分配器（教学资源算法）。
//!
//! 把物理内存建模为 `total_frames` 个 4 KiB 帧的池：
//! - 小页 / 页表：分配 1 帧（天然 4 KiB 对齐）；
//! - 大页：分配 1024 个连续帧，且首帧按 1024 帧对齐（4 MiB 对齐）。
//!
//! 分配失败返回 [`DomainError::ResourceExhausted`]，绝不 panic、绝不静默截断。

use crate::error::{DomainError, DomainResult, ResourceKind};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FrameSnapshot {
    pub total: u64,
    /// 空闲帧号集合（有序，便于持久化与核对）。
    pub free: Vec<u64>,
    pub used: u64,
    pub peak: u64,
}

#[derive(Debug, Clone)]
pub struct FrameAllocator {
    total: u64,
    free: BTreeSet<u64>,
    used: u64,
    peak: u64,
}

impl FrameAllocator {
    pub fn new(total: u64) -> Self {
        Self {
            total,
            free: (0..total).collect(),
            used: 0,
            peak: 0,
        }
    }

    pub fn total(&self) -> u64 {
        self.total
    }
    pub fn available(&self) -> u64 {
        self.free.len() as u64
    }
    pub fn used(&self) -> u64 {
        self.used
    }
    pub fn peak(&self) -> u64 {
        self.peak
    }

    fn fail(&self, requested: u64) -> DomainError {
        DomainError::ResourceExhausted(ResourceKind::Frames {
            requested,
            available: self.available(),
        })
    }

    /// 分配 `count` 个连续帧，起始帧号必须是 `align_frames` 的倍数。
    /// 返回起始物理地址（帧号 × 4 KiB）。
    pub fn allocate_aligned(&mut self, count: u64, align_frames: u64) -> DomainResult<u64> {
        debug_assert!(count >= 1);
        debug_assert!(align_frames >= 1 && align_frames.is_power_of_two());
        if self.available() < count {
            return Err(self.fail(count));
        }

        // 在有序空闲集合上做一次连续段扫描；只在对齐起点处成交。
        let mut run_start: Option<u64> = None;
        let mut run_len: u64 = 0;
        for &f in self.free.range(..) {
            match run_start {
                Some(start) if f == start + run_len => run_len += 1,
                _ => {
                    run_start = Some(f);
                    run_len = 1;
                }
            }
            let start = run_start.expect("run_start just set");
            // 段内第一个满足对齐的候选起点。
            let aligned = (start + align_frames - 1) & !(align_frames - 1);
            if aligned + count <= start + run_len {
                return self.take(aligned, count);
            }
        }
        Err(self.fail(count))
    }

    /// 分配单帧（4 KiB 对齐天然成立）。
    pub fn allocate_one(&mut self) -> DomainResult<u64> {
        self.allocate_aligned(1, 1)
    }

    fn take(&mut self, start_frame: u64, count: u64) -> DomainResult<u64> {
        for f in start_frame..start_frame + count {
            if !self.free.remove(&f) {
                // 不变量破坏：扫描与取出之间集合不应变化（单线程持锁）。
                return Err(DomainError::Computation(format!(
                    "帧分配内部错误：帧 {f} 已被占用"
                )));
            }
        }
        self.used += count;
        self.peak = self.peak.max(self.used);
        Ok(start_frame * crate::config::PAGE_SIZE)
    }

    /// 释放 `[pa, pa + count*4K)`；重复释放返回计算失败而非破坏集合。
    pub fn free(&mut self, pa: u64, count: u64) -> DomainResult<()> {
        if !pa.is_multiple_of(crate::config::PAGE_SIZE) {
            return Err(DomainError::Computation(format!(
                "释放物理地址 {pa:#x} 未按 4 KiB 对齐"
            )));
        }
        let start = pa / crate::config::PAGE_SIZE;
        if start.checked_add(count).is_none_or(|end| end > self.total) {
            return Err(DomainError::Computation(format!(
                "释放范围 [{start}..{start}+{count}] 超出帧池容量 {}",
                self.total
            )));
        }
        for f in start..start + count {
            if !self.free.insert(f) {
                return Err(DomainError::Computation(format!(
                    "帧 {f} 被重复释放（可能页表记账不一致）"
                )));
            }
        }
        self.used = self.used.saturating_sub(count);
        Ok(())
    }

    /// 预留从 `pa` 开始的 `count` 帧（显式物理地址映射）。
    /// 任一帧越界或已占用即整体失败（不做部分预留）。
    pub fn reserve_range(&mut self, pa: u64, count: u64) -> DomainResult<()> {
        if !pa.is_multiple_of(crate::config::PAGE_SIZE) {
            return Err(DomainError::Input(format!(
                "物理地址 {pa:#x} 未按 4 KiB 对齐"
            )));
        }
        let start = pa / crate::config::PAGE_SIZE;
        let end = start
            .checked_add(count)
            .ok_or_else(|| DomainError::Computation("物理区间端址加法溢出".into()))?;
        if end > self.total {
            return Err(DomainError::Input(format!(
                "物理区间 {pa:#x}..+{} 超出帧池容量（{} 帧）",
                count * crate::config::PAGE_SIZE,
                self.total
            )));
        }
        for f in start..end {
            if !self.free.contains(&f) {
                return Err(DomainError::StateConflict(
                    crate::error::ConflictKind::PhysicalRangeOccupied {
                        pa: f * crate::config::PAGE_SIZE,
                        frames: 1,
                    },
                ));
            }
        }
        for f in start..end {
            self.free.remove(&f);
        }
        self.used += count;
        self.peak = self.peak.max(self.used);
        Ok(())
    }

    /// 判断帧号当前是否空闲（持久化校验用）。
    pub fn is_free(&self, frame: u64) -> bool {
        self.free.contains(&frame)
    }

    pub fn snapshot(&self) -> FrameSnapshot {
        FrameSnapshot {
            total: self.total,
            free: self.free.iter().copied().collect(),
            used: self.used,
            peak: self.peak,
        }
    }

    pub fn restore(snap: FrameSnapshot) -> Self {
        let free: BTreeSet<u64> = snap.free.into_iter().collect();
        let used = snap.total.saturating_sub(free.len() as u64);
        Self {
            total: snap.total,
            free,
            used,
            peak: snap.peak.max(used),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::error::ConflictKind;

    #[test]
    fn allocates_and_frees_single_frames() {
        let mut a = FrameAllocator::new(4);
        let p0 = a.allocate_one().unwrap();
        let p1 = a.allocate_one().unwrap();
        assert_eq!(p0, 0);
        assert_eq!(p1, 4096);
        assert_eq!(a.available(), 2);
        a.free(p0, 1).unwrap();
        assert_eq!(a.available(), 3);
        // 回到池中后可再次分配。
        assert_eq!(a.allocate_one().unwrap(), 0);
    }

    #[test]
    fn large_allocation_is_4m_aligned() {
        let mut a = FrameAllocator::new(4096);
        // 制造碎片后，大页仍必须落在 1024 帧边界上。
        a.allocate_one().unwrap(); // 占用帧 0
        let pa = a.allocate_aligned(1024, 1024).unwrap();
        assert_eq!(pa % (4 * 1024 * 1024), 0);
        assert_eq!(pa, 4 * 1024 * 1024);
    }

    #[test]
    fn exhaustion_reports_requested_and_available() {
        let mut a = FrameAllocator::new(2);
        a.allocate_one().unwrap();
        a.allocate_one().unwrap();
        let err = a.allocate_aligned(1024, 1024).unwrap_err();
        match err {
            DomainError::ResourceExhausted(ResourceKind::Frames {
                requested,
                available,
            }) => {
                assert_eq!(requested, 1024);
                assert_eq!(available, 0);
            }
            other => panic!("期望帧耗尽，实际 {other:?}"),
        }
    }

    #[test]
    fn double_free_is_rejected() {
        let mut a = FrameAllocator::new(2);
        let pa = a.allocate_one().unwrap();
        a.free(pa, 1).unwrap();
        assert!(a.free(pa, 1).is_err());
    }

    #[test]
    fn reserve_range_succeeds_and_blocks_reuse() {
        let mut a = FrameAllocator::new(8);
        a.reserve_range(0x2000, 2).unwrap(); // 帧 2、3
        assert!(!a.is_free(2));
        assert!(a.is_free(0));
        // 重叠预留必须失败（物理区间占用冲突）。
        match a.reserve_range(0x3000, 2) {
            Err(DomainError::StateConflict(ConflictKind::PhysicalRangeOccupied { .. })) => {}
            other => panic!("期望物理区间占用冲突，实际 {other:?}"),
        }
        // 未对齐地址是输入错误。
        assert!(matches!(
            a.reserve_range(0x100, 1),
            Err(DomainError::Input(_))
        ));
        // 越界是输入错误。
        assert!(matches!(
            a.reserve_range(0x8000, 1),
            Err(DomainError::Input(_))
        ));
        // 释放后可重新预留。
        a.free(0x2000, 2).unwrap();
        a.reserve_range(0x2000, 2).unwrap();
    }

    #[test]
    fn restore_rebuilds_free_set_and_used_count() {
        let mut a = FrameAllocator::new(8);
        a.allocate_aligned(4, 4).unwrap();
        let snap = a.snapshot();
        assert_eq!(snap.used, 4);
        let b = FrameAllocator::restore(snap);
        assert_eq!(b.used(), 4);
        assert_eq!(b.available(), 4);
    }
}
