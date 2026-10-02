//! SCAN（电梯）调度器。
//!
//! 规则：
//! - 队列按 LBA 排序；磁头沿当前扫描方向服务“前进方向上最近”的请求。
//! - 前进方向上没有更多请求时掉头，原因记为 `scan_sweep:<dir>(reverse)`。
//! - 正常沿方向服务记为 `scan_sweep:<dir>`。
//! - 选中后向后合并同方向且扇区相邻的请求（保留各原始身份）。
//!
//! 注意：SCAN 本身不感知截止期限，因此可能让远离磁头的请求等待很久——
//! 这正是与 deadline 调度器对比的意义所在。

use super::{Dispatch, QueuedRequest, Scheduler, extend_merge, extend_merge_backward};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Sweep {
    Up,
    Down,
}

impl Sweep {
    fn as_str(&self) -> &'static str {
        match self {
            Sweep::Up => "up",
            Sweep::Down => "down",
        }
    }
}

pub struct ScanScheduler {
    /// 始终按 (lba, 到达顺序) 排序。
    queue: Vec<QueuedRequest>,
    sweep: Sweep,
}

impl Default for ScanScheduler {
    fn default() -> Self {
        Self::new()
    }
}

impl ScanScheduler {
    pub fn new() -> Self {
        ScanScheduler {
            queue: Vec::new(),
            sweep: Sweep::Up,
        }
    }

    /// 取出以 `idx` 为锚点的合并组：上行向高 LBA 扩展，下行向低 LBA 扩展。
    /// 返回的 `requests` 按实际服务顺序排列（下行为 LBA 降序）。
    fn pop_batch(&mut self, idx: usize, reason: String) -> Dispatch {
        let (begin, end) = match self.sweep {
            Sweep::Up => (idx, extend_merge(&self.queue, idx)),
            Sweep::Down => (extend_merge_backward(&self.queue, idx), idx),
        };
        let mut requests: Vec<QueuedRequest> = self.queue.drain(begin..=end).collect();
        if self.sweep == Sweep::Down {
            requests.reverse();
        }
        Dispatch { requests, reason }
    }
}

impl Scheduler for ScanScheduler {
    fn name(&self) -> &'static str {
        "scan"
    }

    fn enqueue(&mut self, req: QueuedRequest) {
        // 按 LBA 有序插入；LBA 相同按到达先后保持稳定。
        let pos = self
            .queue
            .iter()
            .position(|q| q.lba > req.lba || (q.lba == req.lba && q.arrival_ms > req.arrival_ms))
            .unwrap_or(self.queue.len());
        self.queue.insert(pos, req);
    }

    fn remove_queued(&mut self, request_id: &str) -> bool {
        if let Some(pos) = self.queue.iter().position(|r| r.id == request_id) {
            self.queue.remove(pos);
            true
        } else {
            false
        }
    }

    fn pick_next(&mut self, _now_ms: f64, head_lba: u64) -> Option<Dispatch> {
        if self.queue.is_empty() {
            return None;
        }

        // 当前方向上的候选：up 取 lba >= head 的最小者；down 取 lba <= head 的最大者。
        let candidate = match self.sweep {
            Sweep::Up => self.queue.iter().position(|q| q.lba >= head_lba),
            Sweep::Down => self.queue.iter().rposition(|q| q.lba <= head_lba),
        };

        let (idx, reason) = match candidate {
            Some(idx) => (idx, format!("scan_sweep:{}", self.sweep.as_str())),
            None => {
                // 掉头：换方向后取该方向上离磁头最近者。
                self.sweep = match self.sweep {
                    Sweep::Up => Sweep::Down,
                    Sweep::Down => Sweep::Up,
                };
                let idx = match self.sweep {
                    Sweep::Up => self.queue.iter().position(|q| q.lba >= head_lba),
                    Sweep::Down => self.queue.iter().rposition(|q| q.lba <= head_lba),
                }
                // 队列非空时，掉头后必有候选（另一端一定有请求）。
                .unwrap_or(0);
                (idx, format!("scan_sweep:{}(reverse)", self.sweep.as_str()))
            }
        };

        Some(self.pop_batch(idx, reason))
    }

    fn queued_len(&self) -> usize {
        self.queue.len()
    }
}
