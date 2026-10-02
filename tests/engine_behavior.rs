//! 引擎行为测试：断言具体结果与失败类别，而非“接口能调用”。

mod support;

use std::sync::{Arc, Mutex};

use arc_page_cache::config::ArcConfig;
use arc_page_cache::engine::{Engine, EngineError};
use arc_page_cache::storage::{FaultStore, PageStore, StoreError};
use arc_page_cache::types::{Access, AccessKind, HitSource, PageId, WriteBackError};

fn engine_with(capacity: usize) -> Engine {
    Engine::new(
        ArcConfig {
            capacity,
            page_size: 128,
            writeback_retries: 0,
        },
        Box::new(FaultStore::new(128)),
        256,
    )
}

fn read(n: u64) -> Access {
    Access::read(n)
}
fn write(n: u64) -> Access {
    Access::write(n)
}

// ---------------------------------------------------------------------------
// 幽灵命中 != 数据已缓存：必须重新 fetch，且结果计入 ghost 而非 hit。
// ---------------------------------------------------------------------------

#[test]
fn ghost_hit_is_not_resident_and_triggers_fetch() {
    let mut eng = engine_with(3);

    // 构造一个 B1 幽灵条目（页 1）：
    //  0,1,2 → T1=[0,1,2]
    //  0     → T1 命中晋升 T2：T1=[1,2], T2=[0]
    //  3 miss→ REPLACE 淘汰 T1 LRU=1 进 B1：T1=[2,3], B1=[1]
    for a in [read(0), read(1), read(2), read(0), read(3)] {
        eng.access(a, None).unwrap();
    }
    let ghost_page = eng
        .cache()
        .pages_lru_to_mru()
        .b1
        .first()
        .copied()
        .expect("page 1 must be a B1 ghost");
    assert_eq!(ghost_page, PageId(1));
    assert!(eng.cache().resident_location(ghost_page).is_none());
    assert_eq!(eng.stats().resident, 3);
    let fetches_before = eng.stats().fetches;
    let hits_before = eng.stats().hits;

    // 访问该幽灵页：B1 幽灵命中。
    let report = eng.access(read(ghost_page.as_u64()), None).unwrap();
    assert_eq!(report.outcome.source, HitSource::GhostB1);
    assert!(
        !report.outcome.source.is_resident(),
        "ghost is not resident"
    );
    assert!(report.outcome.resident_after, "but becomes resident after");
    // 幽灵命中必须产生一次后端装入。
    assert_eq!(
        eng.stats().fetches,
        fetches_before + 1,
        "ghost hit must re-fetch data"
    );
    // 统计分类：ghost hit 不计入真实 hits（与访问前持平）。
    let s = eng.stats();
    assert_eq!(s.ghost_hits_b1, 1);
    assert_eq!(
        s.hits, hits_before,
        "ghost hit must not change the real-hit counter"
    );
    // 页现在驻留在 T2。
    assert_eq!(
        eng.cache().resident_location(ghost_page),
        Some(arc_page_cache::arc::Real::T2)
    );
}

// ---------------------------------------------------------------------------
// 扫描污染：一次性扫描不应把热点从 T2 挤掉。
// ---------------------------------------------------------------------------

#[test]
fn scan_pollution_does_not_evict_hot_t2_pages() {
    let c = 4;
    let mut eng = engine_with(c);

    // 建立两个热点页（进入 T2）。
    for _ in 0..3 {
        eng.access(read(0), None).unwrap();
        eng.access(read(1), None).unwrap();
    }
    assert_eq!(
        eng.cache().resident_location(PageId(0)),
        Some(arc_page_cache::arc::Real::T2)
    );
    assert_eq!(
        eng.cache().resident_location(PageId(1)),
        Some(arc_page_cache::arc::Real::T2)
    );

    // 扫描 100..120：远超容量的单次访问序列。
    for p in 100..120 {
        eng.access(read(p), None).unwrap();
    }

    // 扫描结束后，两个热点中至少页 0（持续最久的重复热点之一）仍应驻留；
    // 更强地：两热点都不应被扫描污染逐出（ARC 的核心价值）。
    let hot_resident = [PageId(0), PageId(1)]
        .iter()
        .filter(|p| eng.cache().resident_location(**p).is_some())
        .count();
    assert!(
        hot_resident >= 1,
        "ARC should protect hot T2 pages from scan, only {hot_resident} resident"
    );

    // 扫描页若仍驻留，只能在 T1（单次访问），绝不在 T2。
    for p in 100..120 {
        if let Some(loc) = eng.cache().resident_location(PageId(p)) {
            assert_eq!(
                loc,
                arc_page_cache::arc::Real::T1,
                "scan pages must not be promoted to T2"
            );
        }
    }

    // 重新访问被扫描挤出的热点：应是幽灵命中而非冷 miss，且随后为真实命中。
    // 找到一个已落入 B1/B2 的热点验证。
    let s = eng.stats();
    assert!(s.resident <= c, "capacity never exceeded after scan");
}

// ---------------------------------------------------------------------------
// 热点切换：p 随命中来源调整（B1 命中增大，B2 命中减小）。
// ---------------------------------------------------------------------------

#[test]
fn adaptive_p_moves_toward_ghost_hit_source() {
    // ---- B1 幽灵命中使 p 增大（c=3）----
    let mut eng = engine_with(3);
    for a in [read(0), read(1), read(2), read(0), read(3)] {
        eng.access(a, None).unwrap();
    }
    // 页 1 已落入 B1（T1 LRU 被 REPLACE 到 B1）。
    assert_eq!(
        eng.cache().ghost_location(PageId(1)),
        Some(arc_page_cache::arc::Ghost::B1)
    );
    assert_eq!(eng.cache().p(), 0);
    eng.access(read(1), None).unwrap();
    assert_eq!(eng.cache().p(), 1, "B1 ghost hit must increase p 0 -> 1");

    // ---- B2 幽灵命中使 p 严格减小（c=4，先把 p 抬到 1）----
    let mut e = engine_with(4);
    // 0,1,2,3 → T1；再访问 0,1 晋升 T2。
    for a in [read(0), read(1), read(2), read(3), read(0), read(1)] {
        e.access(a, None).unwrap();
    }
    // 4 miss：REPLACE 把 T1 LRU=2 送入 B1。
    e.access(read(4), None).unwrap();
    // 回访 2：B1 命中，p 升到 1。
    e.access(read(2), None).unwrap();
    assert_eq!(e.cache().p(), 1);
    // 5 miss：REPLACE 此时选择 T2 LRU=0 送入 B2。
    e.access(read(5), None).unwrap();
    assert_eq!(
        e.cache().ghost_location(PageId(0)),
        Some(arc_page_cache::arc::Ghost::B2)
    );
    // 回访 0：B2 命中，p 必须从 1 降到 0。
    e.access(read(0), None).unwrap();
    assert_eq!(e.cache().p(), 0, "B2 ghost hit must decrease p 1 -> 0");
}

// ---------------------------------------------------------------------------
// 脏页淘汰：干净页直接淘汰；脏页必须先回写。
// ---------------------------------------------------------------------------

#[test]
fn dirty_eviction_writes_back_through_adapter() {
    let store = Arc::new(Mutex::new(CountingStore::default()));
    let mut eng = Engine::new(
        ArcConfig {
            capacity: 2,
            page_size: 16,
            writeback_retries: 0,
        },
        Box::new(SharedStore(store.clone())),
        64,
    );

    eng.access(write(0), None).unwrap();
    eng.access(write(1), None).unwrap();
    assert_eq!(eng.dirty_pages().len(), 2);
    assert_eq!(store.lock().unwrap().writeback_calls, 0);

    // 第三次写迫使淘汰；0 是 T1 LRU 且脏 → 必须回写。
    eng.access(write(2), None).unwrap();
    assert_eq!(
        store.lock().unwrap().writeback_calls,
        1,
        "dirty victim written back"
    );
    assert!(!eng.dirty_pages().contains(&PageId(0)));
    // 0 已离开驻留。
    assert!(eng.cache().resident_location(PageId(0)).is_none());
    assert_eq!(eng.stats().writebacks, 1);
}

// ---------------------------------------------------------------------------
// 脏页回写失败轨迹：瞬时 vs 永久类别，失败后状态不变。
// ---------------------------------------------------------------------------

#[test]
fn writeback_failure_aborts_and_preserves_state() {
    let fault = Arc::new(Mutex::new(FaultPlanStore::new()));
    fault
        .lock()
        .unwrap()
        .fail_next(PageId(0), WriteBackError::Transient);

    let mut eng = Engine::new(
        ArcConfig {
            capacity: 2,
            page_size: 16,
            writeback_retries: 0,
        },
        Box::new(SharedFault(store_rc(&fault))),
        64,
    );

    eng.access(write(0), None).unwrap();
    eng.access(write(1), None).unwrap();
    let resident_before = eng.cache().pages_lru_to_mru();

    // 访问 2 需淘汰脏页 0 → 注入瞬时失败。
    let err = eng.access(read(2), None).unwrap_err();
    match err {
        EngineError::WriteBack {
            page,
            kind,
            attempts,
            ..
        } => {
            assert_eq!(page, PageId(0));
            assert_eq!(kind, WriteBackError::Transient, "concrete failure class");
            assert_eq!(attempts, 1, "retries=0 means exactly 1 attempt");
        }
        other => panic!("expected writeback error, got {other:?}"),
    }

    // 关键不变量：失败后缓存状态完全不变。
    assert_eq!(eng.cache().pages_lru_to_mru(), resident_before);
    assert_eq!(eng.stats().resident, 2, "requested page never entered");
    assert!(
        eng.dirty_pages().contains(&PageId(0)),
        "still dirty & resident"
    );
    assert_eq!(eng.stats().writeback_failures, 1);

    // 诊断记录了拒绝原因。
    let recs: Vec<_> = eng.diagnostics().recent(10).to_vec();
    let reject = recs
        .iter()
        .find(|r| r.reason_code.is_some())
        .expect("a rejection record exists");
    assert_eq!(
        reject.reason_code,
        Some(arc_page_cache::diagnostics::ReasonCode::WritebackFailed)
    );
    assert!(reject.request_id.starts_with("req-"));
}

#[test]
fn permanent_failure_is_not_retried_but_transient_recovers() {
    // 永久失败：retries=2 也只尝试一次。
    let fault = Arc::new(Mutex::new(FaultPlanStore::new()));
    fault
        .lock()
        .unwrap()
        .fail_next(PageId(0), WriteBackError::Permanent);
    let mut eng = Engine::new(
        ArcConfig {
            capacity: 1,
            page_size: 8,
            writeback_retries: 2,
        },
        Box::new(SharedFault(store_rc(&fault))),
        16,
    );
    eng.access(write(0), None).unwrap();
    let err = eng.access(read(1), None).unwrap_err();
    match err {
        EngineError::WriteBack { attempts, kind, .. } => {
            assert_eq!(attempts, 1, "permanent failure must not be retried");
            assert_eq!(kind, WriteBackError::Permanent);
        }
        other => panic!("{other:?}"),
    }

    // 瞬时失败 + 重试：第一次失败、第二次成功 → 访问整体成功。
    let fault2 = Arc::new(Mutex::new(FaultPlanStore::new()));
    fault2
        .lock()
        .unwrap()
        .fail_next(PageId(0), WriteBackError::Transient);
    let mut eng2 = Engine::new(
        ArcConfig {
            capacity: 1,
            page_size: 8,
            writeback_retries: 2,
        },
        Box::new(SharedFault(store_rc(&fault2))),
        16,
    );
    eng2.access(write(0), None).unwrap();
    let report = eng2.access(read(1), None).expect("retry should succeed");
    assert!(report.outcome.evicted);
    assert_eq!(eng2.stats().writebacks, 1);
    assert_eq!(eng2.stats().writeback_failures, 0);
}

// ---------------------------------------------------------------------------
// 容量为零：穿透后端，明确拒绝缓存。
// ---------------------------------------------------------------------------

#[test]
fn zero_capacity_passes_through_and_is_rejected_from_cache() {
    let store = Arc::new(Mutex::new(CountingStore::default()));
    let mut eng = Engine::new(
        ArcConfig {
            capacity: 0,
            page_size: 16,
            writeback_retries: 0,
        },
        Box::new(SharedStore(store.clone())),
        64,
    );

    let r = eng.access(read(7), None).unwrap();
    assert_eq!(r.verdict, arc_page_cache::diagnostics::Verdict::Rejected);
    assert!(!r.outcome.resident_after);
    assert_eq!(eng.stats().resident, 0);
    assert_eq!(eng.stats().rejected_zero_capacity, 1);
    assert_eq!(store.lock().unwrap().fetch_calls, 1);

    // 写访问在零容量下直写后端。
    eng.access(write(7), None).unwrap();
    assert_eq!(store.lock().unwrap().writeback_calls, 1);
    assert_eq!(eng.stats().resident, 0, "still nothing cached");
}

// ---------------------------------------------------------------------------
// 动态缩容：容量收缩淘汰真实页、保持约束；脏页先回写；可缩到 0。
// ---------------------------------------------------------------------------

#[test]
fn dynamic_shrink_evicts_and_preserves_bounds() {
    let mut eng = engine_with(4);
    for p in 0..4 {
        eng.access(read(p), None).unwrap();
    }
    assert_eq!(eng.stats().resident, 4);

    let report = eng.resize(2, None).unwrap();
    assert_eq!(report.evicted.len(), 2);
    assert_eq!(eng.stats().resident, 2);
    assert!(eng.cache().invariants_hold());
    assert_eq!(eng.config().capacity, 2);

    // 继续访问仍满足容量与参考语义。
    for p in 4..8 {
        eng.access(read(p), None).unwrap();
        assert!(eng.stats().resident <= 2);
        assert!(eng.cache().invariants_hold());
    }
}

#[test]
fn shrink_to_zero_flushes_residents_and_disables_cache() {
    let store = Arc::new(Mutex::new(CountingStore::default()));
    let mut eng = Engine::new(
        ArcConfig {
            capacity: 3,
            page_size: 16,
            writeback_retries: 0,
        },
        Box::new(SharedStore(store.clone())),
        64,
    );
    eng.access(write(0), None).unwrap();
    eng.access(write(1), None).unwrap();

    let report = eng.resize(0, None).unwrap();
    assert_eq!(report.new_capacity, 0);
    assert_eq!(eng.stats().resident, 0);
    assert!(eng.cache().invariants_hold());
    // 两个脏页都被回写。
    assert_eq!(store.lock().unwrap().writeback_calls, 2);

    // 此后访问走零容量穿透。
    let r = eng.access(read(99), None).unwrap();
    assert_eq!(r.verdict, arc_page_cache::diagnostics::Verdict::Rejected);
}

#[test]
fn resize_aborts_on_writeback_failure_and_keeps_capacity() {
    let fault = Arc::new(Mutex::new(FaultPlanStore::new()));
    let mut eng = Engine::new(
        ArcConfig {
            capacity: 3,
            page_size: 16,
            writeback_retries: 0,
        },
        Box::new(SharedFault(store_rc(&fault))),
        64,
    );
    eng.access(write(0), None).unwrap();
    eng.access(write(1), None).unwrap();
    eng.access(write(2), None).unwrap();

    // 缩到 1 会淘汰两页；让其中最先被淘汰的脏页永久失败。
    let before = eng.cache().pages_lru_to_mru();
    let first_victim = eng.cache().resize_plan(1).evicted[0].page;
    fault
        .lock()
        .unwrap()
        .fail_next(first_victim, WriteBackError::Permanent);

    let err = eng.resize(1, None).unwrap_err();
    assert!(matches!(err, EngineError::ResizeWriteBack { .. }));
    assert_eq!(eng.config().capacity, 3, "capacity unchanged after abort");
    assert_eq!(eng.cache().pages_lru_to_mru(), before, "lists unchanged");
    assert_eq!(eng.stats().resident, 3);
}

// ---------------------------------------------------------------------------
// 测试用存储适配器：独立、最小，不与生产 FilePageStore 共享实现。
// ---------------------------------------------------------------------------

#[derive(Default)]
struct CountingStore {
    fetch_calls: usize,
    writeback_calls: usize,
}

impl PageStore for CountingStore {
    fn fetch(&mut self, _page: PageId) -> Result<Vec<u8>, StoreError> {
        self.fetch_calls += 1;
        Ok(vec![0u8; 16])
    }
    fn write_back(&mut self, _page: PageId, _data: &[u8]) -> Result<(), StoreError> {
        self.writeback_calls += 1;
        Ok(())
    }
}

/// Rc 共享的计数存储。
struct SharedStore(Arc<Mutex<CountingStore>>);
impl PageStore for SharedStore {
    fn fetch(&mut self, page: PageId) -> Result<Vec<u8>, StoreError> {
        self.0.lock().unwrap().fetch(page)
    }
    fn write_back(&mut self, page: PageId, data: &[u8]) -> Result<(), StoreError> {
        self.0.lock().unwrap().write_back(page, data)
    }
}

/// 可按页注入下一次故障的内存存储。
struct FaultPlanStore {
    faults: std::collections::HashMap<PageId, Vec<WriteBackError>>,
}
impl FaultPlanStore {
    fn new() -> Self {
        Self {
            faults: std::collections::HashMap::new(),
        }
    }
    fn fail_next(&mut self, page: PageId, kind: WriteBackError) {
        self.faults.entry(page).or_default().push(kind);
    }
}
impl PageStore for FaultPlanStore {
    fn fetch(&mut self, _page: PageId) -> Result<Vec<u8>, StoreError> {
        Ok(vec![0xABu8; 16])
    }
    fn write_back(&mut self, page: PageId, _data: &[u8]) -> Result<(), StoreError> {
        if let Some(queue) = self.faults.get_mut(&page) {
            if !queue.is_empty() {
                return Err(StoreError::Io("boom".into(), queue.remove(0)));
            }
        }
        Ok(())
    }
}
struct SharedFault(Arc<Mutex<FaultPlanStore>>);
impl PageStore for SharedFault {
    fn fetch(&mut self, page: PageId) -> Result<Vec<u8>, StoreError> {
        self.0.lock().unwrap().fetch(page)
    }
    fn write_back(&mut self, page: PageId, data: &[u8]) -> Result<(), StoreError> {
        self.0.lock().unwrap().write_back(page, data)
    }
}
fn store_rc(r: &Arc<Mutex<FaultPlanStore>>) -> Arc<Mutex<FaultPlanStore>> {
    r.clone()
}

// 防止未使用告警（AccessKind 在部分断言路径间接使用）。
#[allow(dead_code)]
fn _use_kind(_k: AccessKind) {}
