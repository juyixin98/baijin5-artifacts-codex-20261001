//! deadline 调度器（仿 Linux mq-deadline 的可解释简化版）。
//!
//! 防饥饿规则（每条都会在 Dispatch.reason 中留下可解释记录）：
//! 1. 读、写各一条 FIFO 队列，按到达顺序排列；每个请求带绝对截止时刻。
//! 2. 若某队列队头已过期（now >= deadline），优先下发该队队头，
//!    原因记为 `deadline_expired:<dir>_head expired at t=<deadline> (now=<now>)`。
//!    读写同时过期时，过期得更早的（deadline 更小）优先。
//! 3. 否则下发截止时刻最早的队头，原因记为 `earliest_deadline:<dir> at t=<deadline>`。
//! 4. 写饥饿保护：若连续下发读批次数达到 `writes_starved` 且写队列非空，
//!    强制下发写队头，原因记为 `starvation_guard:writes_starved>=<n>`。

use super::{Dispatch, QueuedRequest, Scheduler, extend_merge};
use crate::model::Direction;
use serde::{Deserialize, Serialize};

/// deadline 调度器可调参数。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeadlineConfig {
    /// 读请求默认相对截止（毫秒）。
    pub read_expire_ms: u64,
    /// 写请求默认相对截止（毫秒）。
    pub write_expire_ms: u64,
    /// 连续服务多少批读之后强制服务写。
    pub writes_starved: u32,
}

impl Default for DeadlineConfig {
    fn default() -> Self {
        DeadlineConfig {
            read_expire_ms: 500,
            write_expire_ms: 5000,
            writes_starved: 2,
        }
    }
}

pub struct DeadlineScheduler {
    config: DeadlineConfig,
    read_fifo: Vec<QueuedRequest>,
    write_fifo: Vec<QueuedRequest>,
    consecutive_read_batches: u32,
}

impl DeadlineScheduler {
    pub fn new(config: DeadlineConfig) -> Self {
        DeadlineScheduler {
            config,
            read_fifo: Vec::new(),
            write_fifo: Vec::new(),
            consecutive_read_batches: 0,
        }
    }

    pub fn default_expire_for(&self, direction: Direction) -> u64 {
        match direction {
            Direction::Read => self.config.read_expire_ms,
            Direction::Write => self.config.write_expire_ms,
        }
    }

    fn fifo_mut(&mut self, direction: Direction) -> &mut Vec<QueuedRequest> {
        match direction {
            Direction::Read => &mut self.read_fifo,
            Direction::Write => &mut self.write_fifo,
        }
    }

    /// 从指定方向的 FIFO 取队头并向后合并相邻同方向请求。
    fn pop_batch(fifo: &mut Vec<QueuedRequest>, reason: String) -> Dispatch {
        let end = extend_merge(fifo, 0);
        let requests: Vec<QueuedRequest> = fifo.drain(0..=end).collect();
        Dispatch { requests, reason }
    }
}

impl Scheduler for DeadlineScheduler {
    fn name(&self) -> &'static str {
        "deadline"
    }

    fn enqueue(&mut self, req: QueuedRequest) {
        // FIFO：直接追加到队尾，保持到达顺序。
        self.fifo_mut(req.direction).push(req);
    }

    fn remove_queued(&mut self, request_id: &str) -> bool {
        for fifo in [&mut self.read_fifo, &mut self.write_fifo] {
            if let Some(pos) = fifo.iter().position(|r| r.id == request_id) {
                fifo.remove(pos);
                return true;
            }
        }
        false
    }

    fn pick_next(&mut self, now_ms: f64, _head_lba: u64) -> Option<Dispatch> {
        let read_head = self.read_fifo.first();
        let write_head = self.write_fifo.first();
        if read_head.is_none() && write_head.is_none() {
            return None;
        }

        // 规则 4：写饥饿保护（优先级最高，因为它是防饥饿的最后防线）。
        if self.consecutive_read_batches >= self.config.writes_starved && write_head.is_some() {
            let reason = format!(
                "starvation_guard:writes_starved>={} (reads served {} batches in a row)",
                self.config.writes_starved, self.consecutive_read_batches
            );
            self.consecutive_read_batches = 0;
            return Some(Self::pop_batch(&mut self.write_fifo, reason));
        }

        // 规则 2：过期队头优先；双方同时过期时 deadline 更小者优先。
        let read_expired = read_head.is_some_and(|r| now_ms >= r.deadline_ms as f64);
        let write_expired = write_head.is_some_and(|r| now_ms >= r.deadline_ms as f64);
        let chosen = if read_expired && write_expired {
            // 两者都过期：选 deadline 更小的；并列时读优先（读通常是同步等待）。
            if read_head.unwrap().deadline_ms <= write_head.unwrap().deadline_ms {
                Direction::Read
            } else {
                Direction::Write
            }
        } else if read_expired {
            Direction::Read
        } else if write_expired {
            Direction::Write
        } else {
            // 规则 3：都未过期，选截止最早的队头；并列时读优先。
            match (read_head, write_head) {
                (Some(r), Some(w)) => {
                    if r.deadline_ms <= w.deadline_ms {
                        Direction::Read
                    } else {
                        Direction::Write
                    }
                }
                (Some(_), None) => Direction::Read,
                (None, Some(_)) => Direction::Write,
                (None, None) => unreachable!(),
            }
        };

        let head = match chosen {
            Direction::Read => self.read_fifo.first().unwrap(),
            Direction::Write => self.write_fifo.first().unwrap(),
        };
        let reason = if now_ms >= head.deadline_ms as f64 {
            format!(
                "deadline_expired:{}_head expired at t={} (now={:.3})",
                chosen.as_str(),
                head.deadline_ms,
                now_ms
            )
        } else {
            format!(
                "earliest_deadline:{} at t={}",
                chosen.as_str(),
                head.deadline_ms
            )
        };

        let dispatch = match chosen {
            Direction::Read => {
                self.consecutive_read_batches += 1;
                Self::pop_batch(&mut self.read_fifo, reason)
            }
            Direction::Write => {
                self.consecutive_read_batches = 0;
                Self::pop_batch(&mut self.write_fifo, reason)
            }
        };
        Some(dispatch)
    }

    fn queued_len(&self) -> usize {
        self.read_fifo.len() + self.write_fifo.len()
    }
}
