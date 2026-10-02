//! ARC（Adaptive Replacement Cache）替换算法。
//!
//! 实现严格遵循 Megiddo & Modha (2003) 的四列表结构：
//!
//! | 列表 | 含义 | 是否持有数据 |
//! |------|------|--------------|
//! | T1 | 仅近期被访问过一次的页（LRU 序） | 是（真实缓存） |
//! | T2 | 至少被访问过两次的页（LRU 序） | 是（真实缓存） |
//! | B1 | 从 T1 淘汰页的幽灵目录（仅元数据） | 否 |
//! | B2 | 从 T2 淘汰页的幽灵目录（仅元数据） | 否 |
//!
//! # 关键大小约束（任何操作后都成立）
//!
//! ```text
//! |T1| + |T2| ≤ c           （真实缓存容量）
//! |T1| + |B1| ≤ c
//! |T2| + |B2| ≤ 2c
//! |T1| + |T2| + |B1| + |B2| ≤ 2c
//! 0 ≤ p ≤ c                 （p = T1 的自适应目标大小）
//! ```
//!
//! # 幽灵命中不是数据命中
//!
//! B1/B2 只保存 [`PageId`]。幽灵命中时页内容**不在**真实缓存中，
//! 必须经后端重新装入（见 [`crate::engine`]），算法只负责调整 p 与列表。
//!
//! # 与引擎的失败原子性
//!
//! 算法分两步：[`ArcCache::plan`] 只计算动作计划（不改变状态），
//! 引擎先完成回写/装入等 I/O，成功后才 [`ArcCache::commit`]。
//! 回写失败时丢弃计划即可，缓存状态保持不变。
//!
//! LRU 序用 [`IndexMap`] 维护：索引 0 为 LRU 端，末尾为 MRU 端。

use indexmap::IndexMap;

use crate::types::{Access, CacheStats, HitSource, PageId};

/// B1/B2 幽灵目录标识。
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Ghost {
    B1,
    B2,
}

/// 真实列表标识。
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Real {
    T1,
    T2,
}

/// 一个真实列表受害者的去向。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VictimDest {
    /// T1 LRU → B1（正常替换）。
    ToB1,
    /// T2 LRU → B2（正常替换）。
    ToB2,
    /// 直接丢弃（仅当 |T1|=c、B1 为空时；幽灵目录没有空位接收它）。
    Drop,
}

/// 计划中的真实列表受害者。引擎必须在 commit 前完成脏页回写。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Victim {
    pub page: PageId,
    pub from: Real,
    pub dest: VictimDest,
}

/// 计划中要永久丢弃的幽灵目录条目（无数据、无需回写）。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct GhostDrop {
    pub page: PageId,
    pub from: Ghost,
}

/// 一次访问的完整动作计划（不可直接执行 I/O 的纯数据描述）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Plan {
    /// T1 命中：移动到 T2 的 MRU 端。
    HitT1 { x: PageId },
    /// T2 命中：提升到 T2 的 MRU 端。
    HitT2 { x: PageId },
    /// B1 幽灵命中：增大 p，替换一页，x 从 B1 删除并装入 T2。
    GhostB1 {
        x: PageId,
        p_new: usize,
        victim: Victim,
    },
    /// B2 幽灵命中：减小 p，替换一页，x 从 B2 删除并装入 T2。
    GhostB2 {
        x: PageId,
        p_new: usize,
        victim: Victim,
    },
    /// 完全缺页。
    Miss {
        x: PageId,
        /// 需淘汰的真实页（若有；脏页需回写）。
        victim: Option<Victim>,
        /// 需永久丢弃的幽灵条目（若有）。
        ghost_drop: Option<GhostDrop>,
    },
}

impl Plan {
    pub fn source(&self) -> HitSource {
        match self {
            Plan::HitT1 { .. } => HitSource::T1,
            Plan::HitT2 { .. } => HitSource::T2,
            Plan::GhostB1 { .. } => HitSource::GhostB1,
            Plan::GhostB2 { .. } => HitSource::GhostB2,
            Plan::Miss { .. } => HitSource::Miss,
        }
    }

    pub fn requested(&self) -> PageId {
        match self {
            Plan::HitT1 { x }
            | Plan::HitT2 { x }
            | Plan::GhostB1 { x, .. }
            | Plan::GhostB2 { x, .. }
            | Plan::Miss { x, .. } => *x,
        }
    }

    /// 需要从真实列表淘汰的页（引擎据此回写脏页）。
    pub fn victim(&self) -> Option<Victim> {
        match self {
            Plan::GhostB1 { victim, .. } | Plan::GhostB2 { victim, .. } => Some(*victim),
            Plan::Miss { victim, .. } => *victim,
            _ => None,
        }
    }

    /// commit 后的自适应目标 p。
    pub fn p_new(&self, current_p: usize) -> usize {
        match self {
            Plan::GhostB1 { p_new, .. } | Plan::GhostB2 { p_new, .. } => *p_new,
            _ => current_p,
        }
    }
}

/// 动态缩容计划。引擎回写全部脏受害者后才能 commit。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResizePlan {
    pub new_c: usize,
    pub new_p: usize,
    /// 从真实列表淘汰的页（按淘汰顺序；脏页需回写）。
    pub evicted: Vec<ResizeVictim>,
    /// 从幽灵目录永久丢弃的页。
    pub dropped_ghosts: Vec<GhostDrop>,
}

/// 缩容中一个真实页的去向（信息完整，commit 时无需重新判定）。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ResizeVictim {
    pub page: PageId,
    pub from: Real,
    pub dest: VictimDest,
}

#[derive(Debug, Clone)]
pub struct ArcCache {
    c: usize,
    p: usize,
    t1: LruList,
    t2: LruList,
    b1: LruList,
    b2: LruList,
    hits_t1: u64,
    hits_t2: u64,
    ghost_b1: u64,
    ghost_b2: u64,
    misses: u64,
}

impl ArcCache {
    /// 以容量 c 创建。`c == 0` 合法：缓存关闭（见引擎层的穿透语义），
    /// 但算法层仍可用——任何 plan 都返回 `Miss` 且不产生驻留；
    /// 引擎负责在 c=0 时走穿透路径并计数“拒绝”。
    pub fn new(c: usize) -> Self {
        Self {
            c,
            p: 0,
            t1: LruList::new(),
            t2: LruList::new(),
            b1: LruList::new(),
            b2: LruList::new(),
            hits_t1: 0,
            hits_t2: 0,
            ghost_b1: 0,
            ghost_b2: 0,
            misses: 0,
        }
    }

    pub fn capacity(&self) -> usize {
        self.c
    }

    pub fn p(&self) -> usize {
        self.p
    }

    pub fn len_t1(&self) -> usize {
        self.t1.len()
    }
    pub fn len_t2(&self) -> usize {
        self.t2.len()
    }
    pub fn len_b1(&self) -> usize {
        self.b1.len()
    }
    pub fn len_b2(&self) -> usize {
        self.b2.len()
    }
    pub fn resident_len(&self) -> usize {
        self.t1.len() + self.t2.len()
    }

    /// 页是否驻留真实缓存；若是，返回所在列表。
    pub fn resident_location(&self, page: PageId) -> Option<Real> {
        if self.t1.contains(page) {
            Some(Real::T1)
        } else if self.t2.contains(page) {
            Some(Real::T2)
        } else {
            None
        }
    }

    /// 幽灵位置（诊断/测试用）。
    pub fn ghost_location(&self, page: PageId) -> Option<Ghost> {
        if self.b1.contains(page) {
            Some(Ghost::B1)
        } else if self.b2.contains(page) {
            Some(Ghost::B2)
        } else {
            None
        }
    }

    /// 按 LRU→MRU 顺序导出各列表内容（逐步对照测试使用）。
    pub fn pages_lru_to_mru(&self) -> ListsSnapshot {
        ListsSnapshot {
            t1: self.t1.pages_lru_to_mru(),
            t2: self.t2.pages_lru_to_mru(),
            b1: self.b1.pages_lru_to_mru(),
            b2: self.b2.pages_lru_to_mru(),
        }
    }

    /// 计算访问动作计划。纯函数：不修改任何状态。
    pub fn plan(&self, access: Access) -> Plan {
        let x = access.page;

        if let Some(_pos) = self.t1.get(x) {
            return Plan::HitT1 { x };
        }
        if let Some(_pos) = self.t2.get(x) {
            return Plan::HitT2 { x };
        }

        if self.b1.contains(x) {
            // Case II：B1 幽灵命中 → p 向 T1 倾斜增大。
            // p = min(p + max(floor(|B2|/|B1|), 1), c)
            let ratio = self.b2.len() / self.b1.len();
            let p_new = (self.p + ratio.max(1)).min(self.c);
            // 论文顺序：先调整 p，REPLACE 必须使用**新** p。
            let victim = self.replace(p_new, Some(Ghost::B1));
            return Plan::GhostB1 { x, p_new, victim };
        }

        if self.b2.contains(x) {
            // Case III：B2 幽灵命中 → p 向 T2 倾斜减小。
            // p = max(p − max(floor(|B1|/|B2|), 1), 0)
            let ratio = self.b1.len() / self.b2.len();
            let p_new = self.p.saturating_sub(ratio.max(1));
            // 先调整 p，再用**新** p 执行 REPLACE（且本次命中来自 B2）。
            let victim = self.replace(p_new, Some(Ghost::B2));
            return Plan::GhostB2 { x, p_new, victim };
        }

        // Case IV：完全缺页。
        let (victim, ghost_drop) = self.plan_miss(x);
        Plan::Miss {
            x,
            victim,
            ghost_drop,
        }
    }

    /// ARC 的 REPLACE 过程：选择一个真实列表受害者。
    ///
    /// `effective_p` 是本次决策使用的自适应目标：幽灵命中时必须传入
    /// **调整后的新 p**（论文先改 p 再 REPLACE）；缺页时传入当前 p。
    /// `ghost_hit` 为本次命中的幽灵目录（若有）。规则：
    /// - 若 T1 非空，且（本次为 B2 命中且 |T1|=c）或 |T1| > effective_p：
    ///   淘汰 T1 LRU → B1；
    /// - 否则淘汰 T2 LRU → B2。
    fn replace(&self, effective_p: usize, ghost_hit: Option<Ghost>) -> Victim {
        let t1_full_for_b2 = ghost_hit == Some(Ghost::B2) && self.t1.len() == self.c;
        if self.t1.len() >= 1 && (t1_full_for_b2 || self.t1.len() > effective_p) {
            Victim {
                page: self.t1.lru().expect("T1 nonempty checked"),
                from: Real::T1,
                dest: VictimDest::ToB1,
            }
        } else {
            Victim {
                page: self.t2.lru().expect("REPLACE invariant: T2 nonempty"),
                from: Real::T2,
                dest: VictimDest::ToB2,
            }
        }
    }

    /// Case IV 的驱逐/丢弃计划。返回 (真实受害者, 永久丢弃的幽灵)。
    fn plan_miss(&self, _x: PageId) -> (Option<Victim>, Option<GhostDrop>) {
        // 容量为零：没有任何列表能容纳页，无受害者、无幽灵丢弃。
        // 引擎层在此之前走穿透路径并单独计数“零容量拒绝”。
        if self.c == 0 {
            return (None, None);
        }
        let l1 = self.t1.len() + self.b1.len();
        let total = l1 + self.t2.len() + self.b2.len();

        if l1 == self.c {
            if self.t1.len() < self.c {
                // |B1| > 0：先永久删除 B1 的 LRU，再 REPLACE。
                let dropped = GhostDrop {
                    page: self.b1.lru().expect("B1 nonempty when T1 < c and L1 = c"),
                    from: Ghost::B1,
                };
                // 缺页时 x 不属于 B2，REPLACE 按 |T1| > p 选择。
                let victim = self.replace(self.p, None);
                (Some(victim), Some(dropped))
            } else {
                // B1 为空且 T1 占满 c：T1 LRU 直接淘汰，不进幽灵目录。
                let victim = Victim {
                    page: self.t1.lru().expect("T1 = c > 0"),
                    from: Real::T1,
                    dest: VictimDest::Drop,
                };
                (Some(victim), None)
            }
        } else if total >= self.c {
            // 目录至少有 c 项（必有真实页占满 c，见模块文档论证）。
            let ghost_drop = if total == 2 * self.c {
                Some(GhostDrop {
                    page: self.b2.lru().expect("B2 nonempty at total = 2c"),
                    from: Ghost::B2,
                })
            } else {
                None
            };
            let victim = self.replace(self.p, None);
            (Some(victim), ghost_drop)
        } else {
            // 目录尚不足 c：直接装入，无淘汰。
            (None, None)
        }
    }

    /// 提交计划。调用方必须已成功完成受害者回写与新页装入。
    pub fn commit(&mut self, plan: &Plan) {
        debug_assert!(self.invariants_hold(), "invariants broken before commit");
        match *plan {
            Plan::HitT1 { x } => {
                self.t1.evict(x);
                self.t2.push_mru(x);
                self.hits_t1 += 1;
            }
            Plan::HitT2 { x } => {
                self.t2.touch(x);
                self.hits_t2 += 1;
            }
            Plan::GhostB1 { x, p_new, victim } => {
                self.b1.evict(x);
                self.apply_victim(victim);
                self.p = p_new;
                self.t2.push_mru(x);
                self.ghost_b1 += 1;
            }
            Plan::GhostB2 { x, p_new, victim } => {
                self.b2.evict(x);
                self.apply_victim(victim);
                self.p = p_new;
                self.t2.push_mru(x);
                self.ghost_b2 += 1;
            }
            Plan::Miss {
                x,
                victim,
                ghost_drop,
            } => {
                if let Some(drop) = ghost_drop {
                    self.ghost_drop(drop);
                }
                if let Some(v) = victim {
                    self.apply_victim(v);
                }
                // 容量为零时页不被任何列表接收（缓存关闭的算法层自洽语义；
                // 正常情况下引擎在更上层短路，不会走到这里）。
                if self.c > 0 {
                    self.t1.push_mru(x);
                }
                self.misses += 1;
            }
        }
        debug_assert!(self.invariants_hold(), "invariants broken after commit");
    }

    fn apply_victim(&mut self, v: Victim) {
        match v.from {
            Real::T1 => {
                let removed = self.t1.evict(v.page);
                debug_assert!(removed);
            }
            Real::T2 => {
                let removed = self.t2.evict(v.page);
                debug_assert!(removed);
            }
        }
        match v.dest {
            VictimDest::ToB1 => self.b1.push_mru(v.page),
            VictimDest::ToB2 => self.b2.push_mru(v.page),
            VictimDest::Drop => {}
        }
    }

    fn ghost_drop(&mut self, drop: GhostDrop) {
        let removed = match drop.from {
            Ghost::B1 => self.b1.evict(drop.page),
            Ghost::B2 => self.b2.evict(drop.page),
        };
        debug_assert!(removed);
    }

    /// 动态容量变更计划（支持扩容与缩容；缩容是重点）。
    ///
    /// - 新 p = min(p, new_c)；
    /// - 用 REPLACE 同样的规则逐页淘汰真实列表，直到 |T1|+|T2| ≤ new_c；
    /// - 再裁剪幽灵目录，恢复 |T1|+|B1| ≤ new_c、总数 ≤ 2·new_c。
    ///
    /// 纯函数，引擎回写脏页成功后调用 [`ArcCache::commit_resize`]。
    pub fn resize_plan(&self, new_c: usize) -> ResizePlan {
        let mut sim = self.clone();
        let new_p = self.p.min(new_c);
        sim.p = new_p;
        sim.c = new_c;

        let mut evicted = Vec::new();
        // 缩容真实列表（扩容时 resident ≤ old_c < new_c，循环不执行）。
        // 受害者一律先进入对应幽灵目录的 MRU 端；幽灵目录的超出部分随后
        // 统一从 LRU 端裁剪——这样保留的是最新元数据（符合 LRU 语义）。
        while sim.t1.len() + sim.t2.len() > new_c {
            let victim = if sim.t1.len() >= 1 && sim.t1.len() > new_p {
                ResizeVictim {
                    page: sim.t1.lru().unwrap(),
                    from: Real::T1,
                    dest: VictimDest::ToB1,
                }
            } else if sim.t2.len() >= 1 {
                ResizeVictim {
                    page: sim.t2.lru().unwrap(),
                    from: Real::T2,
                    dest: VictimDest::ToB2,
                }
            } else {
                // new_c=0 等极端情形：new_p=0 时 T1 不满足 > new_p，
                // 且 T2 为空，强制从 T1 淘汰。
                ResizeVictim {
                    page: sim.t1.lru().unwrap(),
                    from: Real::T1,
                    dest: VictimDest::ToB1,
                }
            };
            sim.apply_resize_victim(victim);
            evicted.push(victim);
        }

        let mut dropped_ghosts = Vec::new();
        // 裁剪 B1：|T1|+|B1| ≤ new_c。
        while sim.t1.len() + sim.b1.len() > new_c {
            let page = sim.b1.lru().expect("B1 must have item to trim");
            sim.b1.evict(page);
            dropped_ghosts.push(GhostDrop {
                page,
                from: Ghost::B1,
            });
        }
        // 裁剪总数 ≤ 2·new_c：优先从 B2 的 LRU 端丢弃。
        while sim.t1.len() + sim.t2.len() + sim.b1.len() + sim.b2.len() > 2 * new_c {
            let page = sim.b2.lru().expect("B2 must have item to trim");
            sim.b2.evict(page);
            dropped_ghosts.push(GhostDrop {
                page,
                from: Ghost::B2,
            });
        }

        debug_assert!(sim.invariants_hold());
        ResizePlan {
            new_c,
            new_p,
            evicted,
            dropped_ghosts,
        }
    }

    /// 提交缩容/扩容计划。调用方必须已完成全部脏受害者回写。
    pub fn commit_resize(&mut self, plan: &ResizePlan) {
        debug_assert!(self.invariants_hold(), "invariants broken before resize");
        for v in &plan.evicted {
            self.apply_resize_victim(*v);
        }
        for d in &plan.dropped_ghosts {
            self.ghost_drop(*d);
        }
        self.c = plan.new_c;
        self.p = plan.new_p;
        debug_assert!(self.invariants_hold(), "invariants broken after resize");
    }

    fn apply_resize_victim(&mut self, v: ResizeVictim) {
        match v.from {
            Real::T1 => assert!(self.t1.evict(v.page)),
            Real::T2 => assert!(self.t2.evict(v.page)),
        }
        match v.dest {
            VictimDest::ToB1 => self.b1.push_mru(v.page),
            VictimDest::ToB2 => self.b2.push_mru(v.page),
            VictimDest::Drop => {}
        }
    }

    /// 检查 ARC 大小约束（测试与 debug 断言使用）。
    pub fn invariants_hold(&self) -> bool {
        let disjoint = {
            use std::collections::HashSet;
            let mut seen: HashSet<PageId> = HashSet::new();
            for l in [&self.t1, &self.t2, &self.b1, &self.b2] {
                for p in l.iter_pages() {
                    if !seen.insert(p) {
                        return false;
                    }
                }
            }
            true
        };
        disjoint
            && self.t1.len() + self.t2.len() <= self.c
            && self.t1.len() + self.b1.len() <= self.c
            && self.t2.len() + self.b2.len() <= 2 * self.c
            && self.t1.len() + self.t2.len() + self.b1.len() + self.b2.len() <= 2 * self.c
            && self.p <= self.c
    }

    /// 仅恢复持久化的自适应目标 p（列表内容不跨进程持久化，见 README 限制）。
    pub fn restore_p(&mut self, p: usize) {
        self.p = p.min(self.c);
    }

    pub fn stats(&self, extra: ExtraCounters) -> CacheStats {
        CacheStats {
            c: self.c,
            p: self.p,
            t1_len: self.t1.len(),
            t2_len: self.t2.len(),
            b1_len: self.b1.len(),
            b2_len: self.b2.len(),
            resident: self.t1.len() + self.t2.len(),
            hits_t1: self.hits_t1,
            hits_t2: self.hits_t2,
            ghost_hits_b1: self.ghost_b1,
            ghost_hits_b2: self.ghost_b2,
            misses: self.misses,
            hits: self.hits_t1 + self.hits_t2,
            rejected_zero_capacity: extra.rejected_zero_capacity,
            writeback_failures: extra.writeback_failures,
            writebacks: extra.writebacks,
            fetches: extra.fetches,
        }
    }
}

/// 引擎维护、算法不感知的计数（回写/装入/零容量拒绝）。
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct ExtraCounters {
    pub rejected_zero_capacity: u64,
    pub writeback_failures: u64,
    pub writebacks: u64,
    pub fetches: u64,
}

/// 四个列表按 LRU→MRU 的内容快照。
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct ListsSnapshot {
    pub t1: Vec<PageId>,
    pub t2: Vec<PageId>,
    pub b1: Vec<PageId>,
    pub b2: Vec<PageId>,
}

/// LRU 列表：IndexMap 索引 0 = LRU，末尾 = MRU。
#[derive(Debug, Clone)]
struct LruList {
    map: IndexMap<PageId, ()>,
}

impl LruList {
    fn new() -> Self {
        Self {
            map: IndexMap::new(),
        }
    }

    fn len(&self) -> usize {
        self.map.len()
    }

    fn contains(&self, page: PageId) -> bool {
        self.map.contains_key(&page)
    }

    fn get(&self, page: PageId) -> Option<usize> {
        self.map.get_index_of(&page)
    }

    fn lru(&self) -> Option<PageId> {
        self.map.get_index(0).map(|(k, ())| *k)
    }

    fn push_mru(&mut self, page: PageId) {
        // 页在四个列表间互斥；防御性地保证不重复插入。
        self.map.shift_remove_entry(&page);
        self.map.insert(page, ());
    }

    fn touch(&mut self, page: PageId) {
        if let Some(idx) = self.map.get_index_of(&page) {
            let last = self.map.len() - 1;
            if idx != last {
                self.map.move_index(idx, last);
            }
        }
    }

    fn evict(&mut self, page: PageId) -> bool {
        self.map.shift_remove(&page).is_some()
    }

    fn iter_pages(&self) -> impl Iterator<Item = PageId> + '_ {
        self.map.keys().copied()
    }

    fn pages_lru_to_mru(&self) -> Vec<PageId> {
        self.map.keys().copied().collect()
    }
}
