//! 引擎：[`ArcCache`]（策略）与 [`PageStore`]（I/O 边界）之间的协调层。
//!
//! # 处理模型
//!
//! 对每次访问严格按以下顺序执行，保证**回写失败不改变缓存状态**：
//!
//! ```text
//! 1. c == 0        → 穿透后端，计入 rejected_zero_capacity，记录“拒绝”
//! 2. cache.plan()  → 纯计算动作计划（不改变状态）
//! 3. 受害者脏页？  → write_back（按配置重试；永久失败不重试）
//!                    失败：丢弃计划，页保持驻留且仍脏，返回错误
//! 4. 幽灵命中/缺页 → fetch 重新装入（幽灵命中也必须装入）
//! 5. cache.commit()→ 列表/统计/p 才真正更新
//! 6. 写访问        → 标记脏页（合成内容确定性修改）
//! ```
//!
//! 引擎持有驻留页的数据映射与脏集合；算法层对它们一无所知。

use std::collections::HashSet;

use crate::arc::{ArcCache, ExtraCounters, Plan, Real, ResizePlan};
use crate::config::ArcConfig;
use crate::diagnostics::{
    new_request_id, DecisionBuilder, Diagnostics, ReasonCode, StateView, Verdict,
};
use crate::storage::{PageData, PageStore, StoreError};
use crate::types::{Access, AccessOutcome, CacheStats, HitSource, PageId, WriteBackError};

/// 一次访问的对外报告。
#[derive(Debug, Clone, serde::Serialize)]
pub struct AccessReport {
    pub request_id: String,
    pub outcome: AccessOutcome,
    pub verdict: Verdict,
}

/// 引擎错误。返回错误时缓存列表状态保证未改变。
#[derive(Debug)]
pub enum EngineError {
    /// 淘汰脏页回写失败：请求页未装入，受害者仍驻留且仍脏。
    WriteBack {
        page: PageId,
        kind: WriteBackError,
        attempts: u32,
        request_id: String,
    },
    /// 装入请求页失败。
    Fetch {
        page: PageId,
        kind: WriteBackError,
        request_id: String,
    },
    /// 缩容回写失败：容量保持不变。
    ResizeWriteBack {
        page: PageId,
        kind: WriteBackError,
        attempts: u32,
    },
}

impl std::fmt::Display for EngineError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            EngineError::WriteBack {
                page,
                kind,
                attempts,
                ..
            } => write!(
                f,
                "write-back of dirty {page} failed after {attempts} attempt(s): {kind}"
            ),
            EngineError::Fetch { page, kind, .. } => {
                write!(f, "fetch of {page} failed: {kind}")
            }
            EngineError::ResizeWriteBack {
                page,
                kind,
                attempts,
            } => write!(
                f,
                "resize aborted: write-back of dirty {page} failed after {attempts} attempt(s): {kind}"
            ),
        }
    }
}

impl std::error::Error for EngineError {}

pub struct Engine {
    cache: ArcCache,
    store: Box<dyn PageStore>,
    config: ArcConfig,
    /// 驻留页内容。键集合与 T1∪T2 严格一致。
    resident: hashbrown_lite::Map<PageId, PageData>,
    dirty: HashSet<PageId>,
    counters: ExtraCounters,
    diagnostics: Diagnostics,
    seq: u64,
}

impl Engine {
    pub fn new(config: ArcConfig, store: Box<dyn PageStore>, diag_capacity: usize) -> Self {
        Self {
            cache: ArcCache::new(config.capacity),
            store,
            config,
            resident: hashbrown_lite::Map::new(),
            dirty: HashSet::new(),
            counters: ExtraCounters::default(),
            diagnostics: Diagnostics::new(diag_capacity),
            seq: 0,
        }
    }

    pub fn cache(&self) -> &ArcCache {
        &self.cache
    }

    pub fn diagnostics(&self) -> &Diagnostics {
        &self.diagnostics
    }

    pub fn config(&self) -> &ArcConfig {
        &self.config
    }

    /// 仅恢复持久化的 p（采样状态），见 [`crate::state`]。
    pub fn restore_adaptive_p(&mut self, p: usize) {
        self.cache.restore_p(p);
    }

    pub fn stats(&self) -> CacheStats {
        self.cache.stats(self.counters)
    }

    /// 脏页标识列表（只暴露 id，不暴露内容）。
    pub fn dirty_pages(&self) -> Vec<PageId> {
        let mut v: Vec<PageId> = self.dirty.iter().copied().collect();
        v.sort();
        v
    }

    /// 页是否驻留且是否脏（诊断接口用）。
    pub fn page_status(&self, page: PageId) -> PageStatus {
        let resident = self.cache.resident_location(page).is_some();
        PageStatus {
            page,
            resident,
            dirty: resident && self.dirty.contains(&page),
            real: self.cache.resident_location(page),
            ghost: self.cache.ghost_location(page),
        }
    }

    fn state_view(&self) -> StateView {
        StateView {
            c: self.cache.capacity(),
            p: self.cache.p(),
            t1_len: self.cache.len_t1(),
            t2_len: self.cache.len_t2(),
            b1_len: self.cache.len_b1(),
            b2_len: self.cache.len_b2(),
            resident: self.cache.resident_len(),
        }
    }

    fn next_request_id(&mut self, explicit: Option<String>) -> (u64, String) {
        self.seq += 1;
        let id = explicit.unwrap_or_else(|| new_request_id(self.seq));
        (self.seq, id)
    }

    /// 处理一次访问。`request_id` 为 None 时自动生成。
    pub fn access(
        &mut self,
        access: Access,
        request_id: Option<String>,
    ) -> Result<AccessReport, EngineError> {
        let (seq, rid) = self.next_request_id(request_id);

        // ---- 容量为零：缓存层关闭，操作穿透后端 -----------------------
        if self.config.capacity == 0 {
            self.counters.rejected_zero_capacity += 1;
            let passthrough = self.passthrough_zero_capacity(access);
            if let Err(e) = passthrough {
                let view = self.state_view();
                DecisionBuilder::new(&mut self.diagnostics, seq, rid.clone()).access(
                    access,
                    Verdict::Undetermined,
                    Some((
                        classify_reason(&e),
                        format!("zero-capacity passthrough backend error: {e}"),
                    )),
                    None,
                    view,
                );
                return Err(match e {
                    StoreError::Io(_, kind) => EngineError::Fetch {
                        page: access.page,
                        kind,
                        request_id: rid,
                    },
                    StoreError::Missing(p) => EngineError::Fetch {
                        page: p,
                        kind: WriteBackError::Permanent,
                        request_id: rid,
                    },
                });
            }
            let outcome = AccessOutcome {
                page: access.page,
                kind: access.kind,
                source: HitSource::Miss,
                resident_after: false,
                evicted: false,
                evicted_page: None,
                p_after: 0,
            };
            let view = self.state_view();
            DecisionBuilder::new(&mut self.diagnostics, seq, rid.clone()).access(
                access,
                Verdict::Rejected,
                Some((
                    ReasonCode::ZeroCapacityPassthrough,
                    "capacity is 0; served from backing store, nothing cached".to_string(),
                )),
                Some(HitSource::Miss),
                view,
            );
            return Ok(AccessReport {
                request_id: rid,
                outcome,
                verdict: Verdict::Rejected,
            });
        }

        // ---- 第 1 步：纯计划 ------------------------------------------
        let plan = self.cache.plan(access);
        let source = plan.source();

        // ---- 第 2 步：受害者回写（在装入请求页之前，避免无谓 I/O） ----
        if let Some(victim) = plan.victim() {
            if self.dirty.contains(&victim.page) {
                if let Err(err) = self.writeback_with_retry(victim.page) {
                    self.counters.writeback_failures += 1;
                    let view = self.state_view();
                    DecisionBuilder::new(&mut self.diagnostics, seq, rid.clone()).access(
                        access,
                        Verdict::Rejected,
                        Some((
                            ReasonCode::WritebackFailed,
                            format!("plan {source:?} aborted before fetch: {err}"),
                        )),
                        Some(source),
                        view,
                    );
                    return Err(err_with_rid(err, victim.page, rid));
                }
            }
        }

        // ---- 第 3 步：非驻留页必须从后端装入（幽灵命中同样要装） ----
        let fetched: Option<PageData> = if !source.is_resident() {
            match self.store.fetch(access.page) {
                Ok(data) => {
                    self.counters.fetches += 1;
                    Some(data)
                }
                Err(e) => {
                    let view = self.state_view();
                    DecisionBuilder::new(&mut self.diagnostics, seq, rid.clone()).access(
                        access,
                        Verdict::Rejected,
                        Some((
                            ReasonCode::FetchFailed,
                            format!("fetch after {source:?} failed: {e}"),
                        )),
                        Some(source),
                        view,
                    );
                    return Err(EngineError::Fetch {
                        page: access.page,
                        kind: e.kind(),
                        request_id: rid,
                    });
                }
            }
        } else {
            None
        };

        // ---- 第 4 步：提交算法状态 ------------------------------------
        let p_before = self.cache.p();
        self.cache.commit(&plan);
        let p_after = plan.p_new(p_before);

        // ---- 第 5 步：同步引擎自己的数据/脏映射 -----------------------
        let evicted_page = self.sync_maps_after_commit(&plan, access, fetched);

        let resident_after = true; // c > 0 且成功提交后请求页必驻留
        let outcome = AccessOutcome {
            page: access.page,
            kind: access.kind,
            source,
            resident_after,
            evicted: evicted_page.is_some(),
            evicted_page,
            p_after,
        };
        let view = self.state_view();
        DecisionBuilder::new(&mut self.diagnostics, seq, rid.clone()).access(
            access,
            Verdict::Accepted,
            None,
            Some(source),
            view,
        );
        Ok(AccessReport {
            request_id: rid,
            outcome,
            verdict: Verdict::Accepted,
        })
    }

    /// c=0 穿透：读直接 fetch；写 fetch→确定性改写→write_back（直写）。
    fn passthrough_zero_capacity(&mut self, access: Access) -> Result<(), StoreError> {
        let mut data = self.store.fetch(access.page)?;
        self.counters.fetches += 1;
        if access.kind.is_write() {
            mutate_page(&mut data);
            self.writeback_direct(access.page, &data)?;
        }
        Ok(())
    }

    /// commit 后维护驻留数据映射与脏集合，返回被淘汰页（若有）。
    fn sync_maps_after_commit(
        &mut self,
        plan: &Plan,
        access: Access,
        fetched: Option<PageData>,
    ) -> Option<PageId> {
        let victim_page = plan.victim().map(|v| v.page);

        // 幽灵命中/缺页：新装入的数据进入驻留映射。
        // 幽灵命中在这里同样插入——证明“幽灵命中 ≠ 已缓存”，
        // 数据是刚刚才从后端取回的。
        if let Some(data) = fetched {
            self.resident.insert(access.page, data);
        }

        // 处理写入：修改驻留内容并标脏。
        if access.kind.is_write() {
            let entry = self
                .resident
                .get_mut(&access.page)
                .expect("page resident after successful commit");
            mutate_page(entry);
            self.dirty.insert(access.page);
        }

        // 受害者若脏，已在 commit 前成功回写；现在移出驻留/脏集合。
        if let Some(vp) = victim_page {
            self.resident.remove(&vp);
            self.dirty.remove(&vp);
        }
        victim_page
    }

    /// 按重试配置回写一个脏页。成功后清除脏标记。
    fn writeback_with_retry(&mut self, page: PageId) -> Result<(), WriteBackFailure> {
        let data = self
            .resident
            .get(&page)
            .expect("dirty victim must have resident data")
            .clone();
        let max_attempts = 1 + self.config.writeback_retries;
        let mut attempts = 0;
        loop {
            attempts += 1;
            match self.store.write_back(page, &data) {
                Ok(()) => {
                    self.dirty.remove(&page);
                    self.counters.writebacks += 1;
                    return Ok(());
                }
                Err(e) => {
                    let kind = e.kind();
                    // 永久失败不重试；瞬时失败用尽配额后报最后一次类别。
                    if kind == WriteBackError::Permanent || attempts >= max_attempts {
                        return Err(WriteBackFailure { kind, attempts });
                    }
                }
            }
        }
    }

    /// c=0 直写路径（独立计数）。
    fn writeback_direct(&mut self, page: PageId, data: &[u8]) -> Result<(), StoreError> {
        let mut attempts = 0;
        let max_attempts = 1 + self.config.writeback_retries;
        loop {
            attempts += 1;
            match self.store.write_back(page, data) {
                Ok(()) => {
                    self.counters.writebacks += 1;
                    return Ok(());
                }
                Err(e) if e.kind() == WriteBackError::Transient && attempts < max_attempts => {
                    continue;
                }
                Err(e) => return Err(e),
            }
        }
    }

    /// 动态调整容量。缩容产生的脏受害者逐个回写；任一失败则**全部中止**，
    /// 容量、列表、驻留映射均保持不变。
    pub fn resize(
        &mut self,
        new_capacity: usize,
        request_id: Option<String>,
    ) -> Result<ResizeReport, EngineError> {
        let (seq, rid) = self.next_request_id(request_id);
        let old_capacity = self.config.capacity;
        let plan: ResizePlan = self.cache.resize_plan(new_capacity);

        // 先回写所有脏受害者（按计划顺序）。
        for v in &plan.evicted {
            if self.dirty.contains(&v.page) {
                if let Err(fail) = self.writeback_with_retry(v.page) {
                    // 注意：此前已成功回写的页脏标记被清除是安全的——
                    // 数据已落盘；列表状态完全未变。
                    let view = self.state_view();
                    let detail = format!(
                        "resize {old_capacity}->{new_capacity} aborted at dirty page {}: {:?} after {} attempt(s)",
                        v.page, fail.kind, fail.attempts
                    );
                    DecisionBuilder::new(&mut self.diagnostics, seq, rid.clone()).event(
                        "resize",
                        Verdict::Rejected,
                        detail,
                        view,
                    );
                    return Err(EngineError::ResizeWriteBack {
                        page: v.page,
                        kind: fail.kind,
                        attempts: fail.attempts,
                    });
                }
            }
        }

        // I/O 全部成功，提交算法状态。
        self.cache.commit_resize(&plan);
        for v in &plan.evicted {
            self.resident.remove(&v.page);
            self.dirty.remove(&v.page);
        }
        let dropped: Vec<PageId> = plan.dropped_ghosts.iter().map(|d| d.page).collect();
        self.config.capacity = new_capacity;

        let p_now = self.cache.p();
        let view = self.state_view();
        let detail = format!(
            "resized c={new_capacity}, p={p_now}, evicted {} real page(s), dropped {} ghost(s)",
            plan.evicted.len(),
            plan.dropped_ghosts.len()
        );
        DecisionBuilder::new(&mut self.diagnostics, seq, rid.clone()).event(
            "resize",
            Verdict::Accepted,
            detail,
            view,
        );
        Ok(ResizeReport {
            request_id: rid,
            new_capacity,
            evicted: plan.evicted.iter().map(|v| v.page).collect(),
            dropped_ghosts: dropped,
        })
    }

    /// 关闭/持久化前把全部脏页回写（T1 LRU→MRU，再 T2 LRU→MRU）。
    /// 任一失败则提前停止并返回失败页；已成功的页保持干净。
    pub fn flush_dirty(&mut self) -> Result<Vec<PageId>, EngineError> {
        let order: Vec<PageId> = self
            .cache
            .pages_lru_to_mru()
            .t1
            .into_iter()
            .chain(self.cache.pages_lru_to_mru().t2)
            .filter(|p| self.dirty.contains(p))
            .collect();
        let mut cleaned = Vec::new();
        for page in order {
            match self.writeback_with_retry(page) {
                Ok(()) => cleaned.push(page),
                Err(fail) => {
                    return Err(EngineError::ResizeWriteBack {
                        page,
                        kind: fail.kind,
                        attempts: fail.attempts,
                    })
                }
            }
        }
        Ok(cleaned)
    }
}

/// 页状态诊断视图（不含内容）。
#[derive(Debug, Clone, serde::Serialize)]
pub struct PageStatus {
    pub page: PageId,
    pub resident: bool,
    pub dirty: bool,
    pub real: Option<Real>,
    pub ghost: Option<crate::arc::Ghost>,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct ResizeReport {
    pub request_id: String,
    pub new_capacity: usize,
    pub evicted: Vec<PageId>,
    pub dropped_ghosts: Vec<PageId>,
}

#[derive(Debug)]
struct WriteBackFailure {
    kind: WriteBackError,
    attempts: u32,
}

impl std::fmt::Display for WriteBackFailure {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} after {} attempt(s)", self.kind, self.attempts)
    }
}

impl std::error::Error for WriteBackFailure {}

fn err_with_rid(fail: WriteBackFailure, page: PageId, rid: String) -> EngineError {
    EngineError::WriteBack {
        page,
        kind: fail.kind,
        attempts: fail.attempts,
        request_id: rid,
    }
}

fn classify_reason(e: &StoreError) -> ReasonCode {
    match e {
        StoreError::Io(_, _) | StoreError::Missing(_) => ReasonCode::FetchFailed,
    }
}

/// 写访问的确定性内容修改：首字节递增。页大小不变、无敏感数据。
fn mutate_page(data: &mut PageData) {
    if let Some(first) = data.first_mut() {
        *first = first.wrapping_add(1);
    }
}

/// 驻留映射：直接用 std HashMap 的薄别名，保持依赖最小。
mod hashbrown_lite {
    use super::PageData;
    use crate::types::PageId;
    use std::collections::HashMap;

    pub type Map<K, V> = HashMap<K, V>;

    // 类型固化，避免误用。
    #[allow(dead_code)]
    pub type Resident = HashMap<PageId, PageData>;
}
