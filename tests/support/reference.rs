//! 独立 ARC 参考模型。
//!
//! 这个模型是评审“逐步对照”的基准，**完全独立**于生产实现
//! [`arc_page_cache::arc`]：
//!
//! - 不 import 生产算法的任何类型（只用标准库 VecDeque/HashSet）；
//! - 直接逐字转写 Megiddo & Modha 论文的 ARC(c) 伪代码（Fig. 1/2），
//!   变量名沿用论文（L1, L2, p）；
//! - 每一步只暴露逐步期望：命中来源、淘汰页、p 值、四个集合大小。
//!
//! 生产实现若与本模型在任意一步不一致，测试失败。
//! 这样测试答案不是“被测核心自己生成”的。
//!
//! 部分辅助方法（sizes/p 等）保留给复核者交互式核对，允许死代码告警。
#![allow(dead_code)]

use std::collections::{HashSet, VecDeque};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RefSource {
    T1,
    T2,
    B1,
    B2,
    Miss,
}

/// 一步的期望结果。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RefStep {
    pub source: RefSource,
    pub evicted: Option<u64>,
    pub p_after: usize,
    pub t1: Vec<u64>,
    pub t2: Vec<u64>,
    pub b1: Vec<u64>,
    pub b2: Vec<u64>,
}

/// 直接照论文实现的 ARC。LRU 在队首，MRU 在队尾。
pub struct ReferenceArc {
    c: usize,
    p: usize,
    t1: VecDeque<u64>,
    t2: VecDeque<u64>,
    b1: VecDeque<u64>,
    b2: VecDeque<u64>,
    members: HashSet<u64>,
}

impl ReferenceArc {
    pub fn new(c: usize) -> Self {
        Self {
            c,
            p: 0,
            t1: VecDeque::new(),
            t2: VecDeque::new(),
            b1: VecDeque::new(),
            b2: VecDeque::new(),
            members: HashSet::new(),
        }
    }

    fn contains(&self, x: u64) -> Option<RefSource> {
        if self.t1.contains(&x) {
            Some(RefSource::T1)
        } else if self.t2.contains(&x) {
            Some(RefSource::T2)
        } else if self.b1.contains(&x) {
            Some(RefSource::B1)
        } else if self.b2.contains(&x) {
            Some(RefSource::B2)
        } else {
            None
        }
    }

    fn remove_from_any(&mut self, x: u64) {
        remove_deque(&mut self.t1, x);
        remove_deque(&mut self.t2, x);
        remove_deque(&mut self.b1, x);
        remove_deque(&mut self.b2, x);
        self.members.remove(&x);
    }

    fn push_mru(&mut self, q: Queue, x: u64) {
        let target = match q {
            Queue::T1 => &mut self.t1,
            Queue::T2 => &mut self.t2,
            Queue::B1 => &mut self.b1,
            Queue::B2 => &mut self.b2,
        };
        remove_deque(target, x);
        target.push_back(x);
        self.members.insert(x);
    }

    fn pop_lru(&mut self, q: Queue) -> u64 {
        let x = match q {
            Queue::T1 => self.t1.pop_front().expect("pop T1"),
            Queue::T2 => self.t2.pop_front().expect("pop T2"),
            Queue::B1 => self.b1.pop_front().expect("pop B1"),
            Queue::B2 => self.b2.pop_front().expect("pop B2"),
        };
        self.members.remove(&x);
        x
    }

    /// 论文 REPLACE 过程。`in_b2` 表示本次命中是否来自 B2（论文 case III）。
    fn replace(&mut self, in_b2: bool) -> u64 {
        let t1_len = self.t1.len();
        let t1_full_for_b2 = in_b2 && t1_len == self.c;
        if t1_len >= 1 && (t1_full_for_b2 || t1_len > self.p) {
            // 删除 L1(T1) 的 LRU，移到 B1。
            let victim = self.pop_lru(Queue::T1);
            self.push_mru(Queue::B1, victim);
            victim
        } else {
            // 删除 L2(T2) 的 LRU，移到 B2。
            let victim = self.pop_lru(Queue::T2);
            self.push_mru(Queue::B2, victim);
            victim
        }
    }

    /// 处理一次访问，返回该步的完整期望。逐字对应论文四个 case。
    pub fn access(&mut self, x: u64) -> RefStep {
        let source = match self.contains(x) {
            Some(s) => s,
            None => RefSource::Miss,
        };
        let mut evicted = None;

        match source {
            // Case I：命中 T1 或 T2。
            RefSource::T1 => {
                self.remove_from_any(x);
                self.push_mru(Queue::T2, x);
            }
            RefSource::T2 => {
                // 已是 T2：移到 MRU（remove+push 幂等）。
                self.remove_from_any(x);
                self.push_mru(Queue::T2, x);
            }
            // Case II：命中 B1。
            RefSource::B1 => {
                let delta = (self.b2.len() / self.b1.len()).max(1);
                self.p = (self.p + delta).min(self.c);
                evicted = Some(self.replace(false));
                self.remove_from_any(x);
                self.push_mru(Queue::T2, x);
            }
            // Case III：命中 B2。
            RefSource::B2 => {
                let delta = (self.b1.len() / self.b2.len()).max(1);
                self.p = self.p.saturating_sub(delta);
                evicted = Some(self.replace(true));
                self.remove_from_any(x);
                self.push_mru(Queue::T2, x);
            }
            // Case IV：未命中。
            RefSource::Miss => {
                if self.c == 0 {
                    // 缓存关闭：不淘汰、不驻留（引擎层会穿透后端）。
                    return RefStep {
                        source,
                        evicted: None,
                        p_after: self.p,
                        t1: deque_to_vec(&self.t1),
                        t2: deque_to_vec(&self.t2),
                        b1: deque_to_vec(&self.b1),
                        b2: deque_to_vec(&self.b2),
                    };
                }
                let l1 = self.t1.len() + self.b1.len();
                let total = l1 + self.t2.len() + self.b2.len();
                if l1 == self.c {
                    // Case A：L1 恰有 c 页。
                    if self.t1.len() < self.c {
                        // 删除 B1 的 LRU，然后 REPLACE。
                        self.pop_lru(Queue::B1);
                        evicted = Some(self.replace(false));
                    } else {
                        // B1 为空：删除 T1 的 LRU（无幽灵目录接收）。
                        evicted = Some(self.pop_lru(Queue::T1));
                    }
                } else if total >= self.c {
                    // Case B：L1 少于 c，L1∪L2 至少 c。
                    if total == 2 * self.c && !self.b2.is_empty() {
                        self.pop_lru(Queue::B2);
                    }
                    evicted = Some(self.replace(false));
                }
                // 把 x 放到 T1 的 MRU。
                self.remove_from_any(x);
                self.push_mru(Queue::T1, x);
            }
        }

        RefStep {
            source,
            evicted,
            p_after: self.p,
            t1: deque_to_vec(&self.t1),
            t2: deque_to_vec(&self.t2),
            b1: deque_to_vec(&self.b1),
            b2: deque_to_vec(&self.b2),
        }
    }

    pub fn sizes(&self) -> (usize, usize, usize, usize) {
        (self.t1.len(), self.t2.len(), self.b1.len(), self.b2.len())
    }

    pub fn p(&self) -> usize {
        self.p
    }
}

#[derive(Clone, Copy)]
enum Queue {
    T1,
    T2,
    B1,
    B2,
}

fn remove_deque(q: &mut VecDeque<u64>, x: u64) {
    if let Some(pos) = q.iter().position(|v| *v == x) {
        q.remove(pos);
    }
}

fn deque_to_vec(q: &VecDeque<u64>) -> Vec<u64> {
    q.iter().copied().collect()
}
