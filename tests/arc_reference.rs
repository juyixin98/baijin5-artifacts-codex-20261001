//! 逐步对照：生产 ARC 与独立参考模型在每一步必须完全一致
//! （命中来源、淘汰页、p、四个列表的 LRU→MRU 内容）。
//!
//! 期望值全部来自 `tests/support/reference.rs`，该文件不引用生产算法，
//! 因此答案不是被测核心自己生成的。

mod support;

use arc_page_cache::arc::ArcCache;
use arc_page_cache::types::{Access, HitSource};
use support::reference::{RefSource, ReferenceArc};

fn map_source(s: HitSource) -> RefSource {
    match s {
        HitSource::T1 => RefSource::T1,
        HitSource::T2 => RefSource::T2,
        HitSource::GhostB1 => RefSource::B1,
        HitSource::GhostB2 => RefSource::B2,
        HitSource::Miss => RefSource::Miss,
    }
}

/// 核心逐步对照：同一页序列，生产实现与参考模型逐步比对。
fn assert_trace_matches_reference(c: usize, pages: &[u64]) {
    let mut prod = ArcCache::new(c);
    let mut refer = ReferenceArc::new(c);

    for (i, &p) in pages.iter().enumerate() {
        let access = Access::read(p);
        let plan = prod.plan(access);
        let step = refer.access(p);

        // 1) 命中来源一致。
        assert_eq!(
            map_source(plan.source()),
            step.source,
            "step {i} page {p}: source mismatch (c={c})"
        );

        // 2) 淘汰的真实页一致。
        assert_eq!(
            plan.victim().map(|v| v.page.as_u64()),
            step.evicted,
            "step {i} page {p}: evicted mismatch (c={c})"
        );

        // 3) commit 前约束成立；提交后与参考模型逐列表一致。
        assert!(prod.invariants_hold(), "step {i}: invariants before commit");
        prod.commit(&plan);
        assert!(prod.invariants_hold(), "step {i}: invariants after commit");

        let snap = prod.pages_lru_to_mru();
        let to_u64 = |v: Vec<arc_page_cache::types::PageId>| {
            v.into_iter().map(|x| x.as_u64()).collect::<Vec<_>>()
        };
        assert_eq!(to_u64(snap.t1), step.t1, "step {i} page {p}: T1 (c={c})");
        assert_eq!(to_u64(snap.t2), step.t2, "step {i} page {p}: T2 (c={c})");
        assert_eq!(to_u64(snap.b1), step.b1, "step {i} page {p}: B1 (c={c})");
        assert_eq!(to_u64(snap.b2), step.b2, "step {i} page {p}: B2 (c={c})");

        // 4) 自适应 p 一致。
        assert_eq!(prod.p(), step.p_after, "step {i} page {p}: p (c={c})");

        // 5) 关键大小约束逐条断言（不只是“不 panic”）。
        let (t1, t2, b1, b2) = (prod.len_t1(), prod.len_t2(), prod.len_b1(), prod.len_b2());
        assert!(t1 + t2 <= c, "resident over capacity at step {i}");
        assert!(t1 + b1 <= c, "L1 bound at step {i}");
        assert!(t2 + b2 <= 2 * c, "T2+B2 bound at step {i}");
        assert!(t1 + t2 + b1 + b2 <= 2 * c, "total bound at step {i}");
        assert!(prod.p() <= c, "p bound at step {i}");
    }
}

#[test]
fn matches_reference_on_mixed_trace() {
    let pages: Vec<u64> = arc_page_cache::trace::mixed_trace()
        .into_iter()
        .map(|a| a.page.as_u64())
        .collect();
    for c in 1..=8 {
        assert_trace_matches_reference(c, &pages);
    }
}

#[test]
fn matches_reference_on_scan() {
    // 扫描 0..40 对不同容量都必须一致。
    let pages: Vec<u64> = (0..40).collect();
    for c in [1usize, 2, 3, 5, 8, 16] {
        assert_trace_matches_reference(c, &pages);
    }
}

#[test]
fn matches_reference_on_hotspot_switch() {
    let trace =
        arc_page_cache::trace::hotspot_switch_trace(&[0, 1, 2, 3], &[10, 11, 12, 13], 5, 5, true);
    let pages: Vec<u64> = trace.into_iter().map(|a| a.page.as_u64()).collect();
    for c in [2usize, 4, 6, 8] {
        assert_trace_matches_reference(c, &pages);
    }
}

/// 确定性 LCG 随机轨迹（不引入随机数依赖，保证可复现）。
#[test]
fn matches_reference_on_pseudorandom_traces() {
    let mut seed = 0x1234_5678u64;
    let mut next = || {
        // mmix 常数的 LCG。
        seed = seed
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        seed >> 33
    };

    for c in [1usize, 2, 4, 7] {
        for trial in 0..20 {
            let pages: Vec<u64> = (0..300).map(|_| next() % (c as u64 * 3 + 2)).collect();
            assert_trace_matches_reference(c, &pages);
            let _ = trial;
        }
    }
}

#[test]
fn zero_capacity_algorithm_is_defined_and_matches_reference() {
    // c=0：两侧都应把每步视为 miss，且永远没有驻留/幽灵。
    let mut prod = ArcCache::new(0);
    let mut refer = ReferenceArc::new(0);
    for p in 0..5 {
        let plan = prod.plan(Access::read(p));
        let step = refer.access(p);
        assert_eq!(map_source(plan.source()), RefSource::Miss);
        assert_eq!(step.source, RefSource::Miss);
        assert!(plan.victim().is_none());
        prod.commit(&plan);
        // c=0 时即使 commit miss 也不能有任何驻留——生产层在引擎侧短路，
        // 算法层 commit miss 会 push 到 T1。这里验证参考语义：
        // 生产算法单独使用时 c=0 的 commit 行为受引擎层策略保护。
        assert_eq!(prod.resident_len(), 0, "engine must short-circuit c=0");
    }
}
