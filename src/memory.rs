//! 物理页帧分配器（空闲帧集合管理的模拟物理内存）。
//!
//! - 分配单元：4KiB 帧，`allocate` 确定性地取**编号最小的空闲帧**；
//! - 帧池容量固定（建机时给定），耗尽返回 [`ServiceError::Exhausted`]；
//! - `release(ppn)` 按帧归还；重复释放（双重回收）返回 [`ServiceError::Compute`]；
//! - **不接触任何真实内核页表**：这里只在一个集合上模拟。

use crate::config::{DEFAULT_FRAME_POOL, PAGE_SIZE};
use crate::errors::{Result, ServiceError};
use std::collections::BTreeSet;

#[derive(Debug)]
pub struct FramePool {
    total: u64,
    /// 当前空闲帧集合（有序，取最小值保证确定性）。
    free: BTreeSet<u64>,
}

impl FramePool {
    pub fn new(total_frames: u64) -> Self {
        FramePool {
            total: total_frames,
            free: (0..total_frames).collect(),
        }
    }

    pub fn allocate(&mut self) -> Result<u64> {
        self.free.pop_first().ok_or_else(|| {
            ServiceError::Exhausted(format!(
                "物理帧池耗尽：{}/{} 帧已分配（每帧 {PAGE_SIZE} 字节）",
                self.used(),
                self.total
            ))
        })
    }

    /// 归还一个帧。
    pub fn release(&mut self, ppn: u64) -> Result<()> {
        if ppn >= self.total {
            return Err(ServiceError::Compute(format!(
                "内部错误：尝试释放越界 PPN {ppn}（帧池容量 {}）",
                self.total
            )));
        }
        if !self.free.insert(ppn) {
            return Err(ServiceError::Compute(format!(
                "内部错误：PPN {ppn} 被重复释放（双重回收）"
            )));
        }
        Ok(())
    }

    /// 快照恢复用：把一批已知占用的 PPN 标记为已分配。
    pub fn reserve_existing(&mut self, ppns: BTreeSet<u64>) -> Result<()> {
        for ppn in &ppns {
            if *ppn >= self.total {
                return Err(ServiceError::Compute(format!(
                    "快照恢复失败：PPN {ppn} 超出帧池容量 {}",
                    self.total
                )));
            }
            self.free.remove(ppn);
        }
        Ok(())
    }

    pub fn used(&self) -> u64 {
        self.total - self.free.len() as u64
    }
    pub fn total(&self) -> u64 {
        self.total
    }
    pub fn available(&self) -> u64 {
        self.free.len() as u64
    }
}

impl Default for FramePool {
    fn default() -> Self {
        FramePool::new(DEFAULT_FRAME_POOL)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn allocates_lowest_free_and_exhausts() {
        let mut pool = FramePool::new(2);
        assert_eq!(pool.allocate().unwrap(), 0);
        assert_eq!(pool.allocate().unwrap(), 1);
        let err = pool.allocate().unwrap_err();
        assert_eq!(err.kind(), "resource_exhausted");
        assert_eq!(pool.used(), 2);
    }

    #[test]
    fn release_makes_frame_reusable_lowest_first() {
        let mut pool = FramePool::new(3);
        let a = pool.allocate().unwrap(); // 0
        let _b = pool.allocate().unwrap(); // 1
        pool.release(a).unwrap();
        // 最低空闲帧重新变为 0。
        assert_eq!(pool.allocate().unwrap(), 0);
        assert_eq!(pool.allocate().unwrap(), 2);
    }

    #[test]
    fn double_release_is_compute_failure() {
        let mut pool = FramePool::new(1);
        let ppn = pool.allocate().unwrap();
        pool.release(ppn).unwrap();
        let err = pool.release(ppn).unwrap_err();
        assert_eq!(err.kind(), "compute_failure");
    }
}
