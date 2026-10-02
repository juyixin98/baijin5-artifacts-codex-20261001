//! 行为契约的核心级验证（不经过 HTTP，直接驱动状态机）。
//!
//! 覆盖任务指定的四类夹具：
//! 1. 同虚址不同进程（ASID 隔离）——物理地址不同，且与独立参考模型一致；
//! 2. 跨页写——拆分段逐段验权，第二页只读时在边界处报权限故障；
//! 3. 权限降级 + TLB 过期——不失效时旧权限继续命中且被标记 stale；
//! 4. 大页/小页覆盖冲突——双向拒绝。
//!
//! 期望答案全部由 tests/common 中的独立参考模型 `RefModel` 计算，
//! 不调用被测核心的翻译代码。

#[path = "common/mod.rs"]
mod common;

use common::harness::lab;
use common::{RefAccess, RefFault, RefModel, RefPage, RefPerms, LARGE as REF_LARGE};
use mmu_lab::error::{DomainError, FaultKind};
use mmu_lab::store::translate::TranslateOutcome;
use mmu_lab::types::{AccessKind, MapRequest, PageSize, Permissions, TranslateRequest};

const RWX: Permissions = Permissions {
    read: true,
    write: true,
    execute: true,
};
const ROX: Permissions = Permissions {
    read: true,
    write: false,
    execute: true,
};

fn map_small(lab: &mut mmu_lab::Lab, asid: u16, va: u64, perms: Permissions, global: bool) -> u64 {
    lab.map(&MapRequest {
        asid,
        va,
        pa: None,
        page: PageSize::Small,
        permissions: perms,
        global,
        invalidate: true,
    })
    .expect("小页映射应成功")
    .pa
}

fn translate(
    lab: &mut mmu_lab::Lab,
    asid: u16,
    va: u64,
    len: u64,
    access: AccessKind,
) -> TranslateOutcome {
    lab.translate(&TranslateRequest {
        asid,
        va,
        access,
        len,
        fetch_pte: false,
    })
    .expect("翻译调用本身不应产生工程错误")
}

fn ref_perms(p: Permissions) -> RefPerms {
    RefPerms {
        r: p.read,
        w: p.write,
        x: p.execute,
    }
}

// ---------------------------------------------------------------------------
// 夹具 1：同虚址不同进程
// ---------------------------------------------------------------------------

#[test]
fn same_va_in_two_processes_translates_to_distinct_frames_and_matches_reference() {
    let mut l = lab(256, 16);
    let p1 = l.create_asid(Some("proc-a".into())).unwrap().0;
    let p2 = l.create_asid(Some("proc-b".into())).unwrap().0;
    assert_ne!(p1, p2);

    let va = 0x1000;
    let pa1 = map_small(&mut l, p1, va, RWX, false);
    let pa2 = map_small(&mut l, p2, va, RWX, false);
    assert_ne!(pa1, pa2, "两个进程的数据帧必须不同");

    // ASID 隔离（在任何 p2 翻译发生前验证）：p1 翻译回填 TLB 后，
    // p2 同 VA 的首次翻译必须走表，不能命中 p1 的条目。
    match translate(&mut l, p1, va, 1, AccessKind::Read) {
        TranslateOutcome::Ok(r) => assert_eq!(r.tlb_segments, 0),
        other => panic!("{other:?}"),
    }
    match translate(&mut l, p2, va, 1, AccessKind::Read) {
        TranslateOutcome::Ok(r) => {
            assert_eq!(r.tlb_segments, 0, "p2 首次翻译不得命中 p1 的 TLB 条目");
            assert_eq!(r.walk_segments, 1);
            assert_eq!(r.pa, pa2);
        }
        other => panic!("{other:?}"),
    }

    // 独立参考模型：PA 来自 SUT 分配器，但翻译/权限/存在性由参考模型独立计算。
    let mut reference = RefModel::new();
    reference.map_small(p1, va, pa1, ref_perms(RWX), false);
    reference.map_small(p2, va, pa2, ref_perms(RWX), false);

    for &probe in &[0x1000u64, 0x1050, 0x1FFF] {
        for asid in [p1, p2] {
            let got = translate(&mut l, asid, probe, 1, AccessKind::Read);
            let want = reference.translate(asid, probe, RefAccess::Read).unwrap();
            match got {
                TranslateOutcome::Ok(r) => {
                    assert_eq!(
                        r.pa, want.pa,
                        "ASID {asid} VA {probe:#x} 的 PA 与参考模型不符"
                    );
                    assert_eq!(r.page, PageSize::Small);
                }
                other => panic!("期望翻译成功，实际 {other:?}"),
            }
        }
    }
}

#[test]
fn unmapped_va_in_other_process_is_not_present_not_permission() {
    let mut l = lab(256, 16);
    let p1 = l.create_asid(None).unwrap().0;
    let p2 = l.create_asid(None).unwrap().0;
    map_small(&mut l, p1, 0x4000, RWX, false);
    // p2 无映射：p1 的 L1 分支属于 p1 的页表，p2 的 L1 槽为空，必须归类为 L1 not-present。
    match translate(&mut l, p2, 0x4100, 1, AccessKind::Read) {
        TranslateOutcome::Fault(f) => match f.kind {
            FaultKind::NotPresent { level, .. } => {
                assert_eq!(level, 1, "p2 的 L1 槽为空，应在 L1 缺页")
            }
            other => panic!("期望 NotPresent，实际 {other:?}"),
        },
        other => panic!("期望故障，实际 {other:?}"),
    }
}

// ---------------------------------------------------------------------------
// 夹具 2：跨页写（拆分段逐段验权）
// ---------------------------------------------------------------------------

#[test]
fn cross_page_write_split_validates_permissions_per_segment() {
    let mut l = lab(256, 16);
    let a = l.create_asid(None).unwrap().0;
    // 第一页 RWX，相邻第二页只读。
    let pa0 = map_small(&mut l, a, 0x1000, RWX, false);
    let pa1 = map_small(&mut l, a, 0x2000, ROX, false);

    let mut reference = RefModel::new();
    reference.map_small(a, 0x1000, pa0, ref_perms(RWX), false);
    reference.map_small(a, 0x2000, pa1, ref_perms(ROX), false);

    // 跨 16+16 字节的写：参考模型逐段判定第二段 Permission。
    let ref_fault = reference
        .translate_range(a, 0x1FF0, 32, RefAccess::Write)
        .unwrap_err();
    assert_eq!(ref_fault, RefFault::Permission);

    match l
        .translate(&TranslateRequest {
            asid: a,
            va: 0x1FF0,
            access: AccessKind::Write,
            len: 32,
            fetch_pte: false,
        })
        .unwrap()
    {
        TranslateOutcome::Fault(f) => {
            assert!(matches!(
                f.kind,
                FaultKind::PermissionDenied {
                    need: AccessKind::Write,
                    writable: false,
                    ..
                }
            ));
            assert_eq!(f.va, 0x2000, "故障地址必须精确到第二段首址");
            assert_eq!(f.fault_source, "walk");
            assert_eq!(
                f.completed_segments.len(),
                1,
                "故障前应保留第一段的中间状态"
            );
            assert_eq!(f.completed_segments[0].va, 0x1FF0);
            assert_eq!(f.completed_segments[0].bytes, 16);
            assert_eq!(f.completed_segments[0].pa, pa0 + 0xFF0);
        }
        other => panic!("期望跨页写权限故障，实际 {other:?}"),
    }

    // 同样区间的读访问两段都允许：返回两个段，PA 与参考模型逐段一致。
    let segs = reference
        .translate_range(a, 0x1FF0, 32, RefAccess::Read)
        .unwrap();
    match translate(&mut l, a, 0x1FF0, 32, AccessKind::Read) {
        TranslateOutcome::Ok(r) => {
            assert_eq!(r.segments.len(), 2);
            assert_eq!(r.segments[0].bytes, 16);
            assert_eq!(r.segments[1].bytes, 16);
            for (got, (v, pa, page)) in r.segments.iter().zip(segs.iter()) {
                assert_eq!(got.va, *v);
                assert_eq!(got.pa, *pa);
                assert_eq!(
                    got.page,
                    if *page == RefPage::Small {
                        PageSize::Small
                    } else {
                        PageSize::Large
                    }
                );
                assert!(
                    got.readable && !got.writable || got.writable,
                    "段权限需如实反映"
                );
            }
        }
        other => panic!("期望跨页读成功，实际 {other:?}"),
    }
}

#[test]
fn cross_page_access_into_unmapped_page_is_not_present_at_boundary() {
    let mut l = lab(256, 16);
    let a = l.create_asid(None).unwrap().0;
    map_small(&mut l, a, 0x1000, RWX, false);
    match l
        .translate(&TranslateRequest {
            asid: a,
            va: 0x1FF0,
            access: AccessKind::Read,
            len: 32,
            fetch_pte: false,
        })
        .unwrap()
    {
        TranslateOutcome::Fault(f) => {
            assert!(matches!(f.kind, FaultKind::NotPresent { level: 2, .. }));
            assert_eq!(f.va, 0x2000);
            assert_eq!(f.completed_segments.len(), 1);
        }
        other => panic!("期望边界处缺页，实际 {other:?}"),
    }
}

// ---------------------------------------------------------------------------
// 夹具 3：权限降级与 TLB 过期
// ---------------------------------------------------------------------------

#[test]
fn permission_downgrade_without_invalidation_keeps_stale_writable_entry() {
    let mut l = lab(256, 16);
    let a = l.create_asid(None).unwrap().0;
    let va = 0x1000;
    let pa = map_small(&mut l, a, va, RWX, false);

    // 首次写：走表并回填，TLB 内为可写条目。
    match translate(&mut l, a, va + 0x20, 1, AccessKind::Write) {
        TranslateOutcome::Ok(r) => {
            assert_eq!(r.pa, pa + 0x20);
            assert_eq!(r.tlb_segments, 0);
        }
        other => panic!("首次写应成功：{other:?}"),
    }

    // 权限降级但显式不失效（过期夹具的关键构造手段）。
    l.protect(a, va, false, ROX, false).unwrap();

    // 页表已是只读：强制走表必为权限故障。
    match l
        .translate(&TranslateRequest {
            asid: a,
            va: va + 0x20,
            access: AccessKind::Write,
            len: 1,
            fetch_pte: true,
        })
        .unwrap()
    {
        TranslateOutcome::Fault(f) => assert!(matches!(f.kind, FaultKind::PermissionDenied { .. })),
        other => panic!("页表降级后强制走表应故障：{other:?}"),
    }

    // 普通翻译仍命中旧 TLB：写“成功”，但该段必须被诊断标记为 stale。
    match translate(&mut l, a, va + 0x20, 1, AccessKind::Write) {
        TranslateOutcome::Ok(r) => {
            assert_eq!(r.tlb_segments, 1);
            assert_eq!(r.stale_segments, 1, "过期条目应被显式标记");
            assert!(r.segments[0].stale);
            assert_eq!(r.pa, pa + 0x20, "过期条目仍按缓存物理地址翻译");
        }
        other => panic!("未失效前旧条目应继续命中：{other:?}"),
    }

    // 执行显式失效协议后，旧权限不再生效。
    let removed = l
        .invalidate(Some(a), Some(va), Some(PageSize::Small))
        .unwrap();
    assert_eq!(removed, 1);
    match translate(&mut l, a, va + 0x20, 1, AccessKind::Write) {
        TranslateOutcome::Fault(f) => {
            assert!(matches!(
                f.kind,
                FaultKind::PermissionDenied {
                    need: AccessKind::Write,
                    ..
                }
            ));
            assert_eq!(f.fault_source, "walk");
        }
        other => panic!("失效后写应被拒绝：{other:?}"),
    }
}

#[test]
fn tlb_capacity_evicts_fifo_and_fill_is_observable() {
    let mut l = lab(4096, 2);
    let a = l.create_asid(None).unwrap().0;
    map_small(&mut l, a, 0x1000, RWX, false);
    map_small(&mut l, a, 0x2000, RWX, false);
    map_small(&mut l, a, 0x3000, RWX, false);

    // 先翻译前两页填满容量 2 的 TLB（FIFO 队首 = 0x1000）。
    translate(&mut l, a, 0x1000, 1, AccessKind::Read);
    translate(&mut l, a, 0x2000, 1, AccessKind::Read);

    // 第三页走表回填时淘汰队首，淘汰事件应在翻译结果中可见。
    let r = match translate(&mut l, a, 0x3000, 1, AccessKind::Read) {
        TranslateOutcome::Ok(r) => r,
        other => panic!("{other:?}"),
    };
    assert_eq!(r.evictions.len(), 1);
    assert_eq!(r.evictions[0].reason, "capacity_fifo");
    assert_eq!(r.evictions[0].vpn_tag, 0x1);
}

// ---------------------------------------------------------------------------
// 夹具 4：大页 / 小页覆盖冲突
// ---------------------------------------------------------------------------

#[test]
fn large_small_overlap_is_rejected_in_both_directions() {
    let mut l = lab(4096, 16);
    let a = l.create_asid(None).unwrap().0;

    // 先大页后小页。
    l.map(&MapRequest {
        asid: a,
        va: 0x0,
        pa: Some(0x0040_0000),
        page: PageSize::Large,
        permissions: RWX,
        global: false,
        invalidate: true,
    })
    .unwrap();
    let err = l
        .map(&MapRequest {
            asid: a,
            va: 0x1000,
            pa: None,
            page: PageSize::Small,
            permissions: RWX,
            global: false,
            invalidate: true,
        })
        .unwrap_err();
    match err {
        DomainError::StateConflict(c) => assert!(matches!(
            c,
            mmu_lab::error::ConflictKind::LargeSmallOverlap(_)
        )),
        other => panic!("期望大小页覆盖冲突，实际 {other:?}"),
    }

    // 相反方向：先小页后大页（新进程，干净地址空间）。
    let b = l.create_asid(None).unwrap().0;
    map_small(&mut l, b, 0x2000, RWX, false);
    let err = l
        .map(&MapRequest {
            asid: b,
            va: 0x0,
            pa: Some(0x0080_0000),
            page: PageSize::Large,
            permissions: RWX,
            global: false,
            invalidate: true,
        })
        .unwrap_err();
    assert!(matches!(
        err,
        DomainError::StateConflict(mmu_lab::error::ConflictKind::LargeSmallOverlap(_))
    ));
}

#[test]
fn duplicate_mapping_is_state_conflict_and_frames_are_not_leaked() {
    let mut l = lab(256, 16);
    let a = l.create_asid(None).unwrap().0;
    map_small(&mut l, a, 0x1000, RWX, false);
    let before = l.frames().available();
    let err = map_small_result(&mut l, a, 0x1000, RWX, false).unwrap_err();
    assert!(matches!(
        err,
        DomainError::StateConflict(mmu_lab::error::ConflictKind::MappingExists { .. })
    ));
    assert_eq!(l.frames().available(), before, "冲突失败不得泄漏帧");
}

fn map_small_result(
    l: &mut mmu_lab::Lab,
    asid: u16,
    va: u64,
    perms: Permissions,
    global: bool,
) -> Result<(), DomainError> {
    l.map(&MapRequest {
        asid,
        va,
        pa: None,
        page: PageSize::Small,
        permissions: perms,
        global,
        invalidate: true,
    })
    .map(|_| ())
}

// ---------------------------------------------------------------------------
// 大页翻译与跨大页区间
// ---------------------------------------------------------------------------

#[test]
fn large_page_translation_covers_four_mib_and_matches_reference() {
    let mut l = lab(8192, 16);
    let a = l.create_asid(None).unwrap().0;
    let va = 0x0080_0000u64;
    let pa = 0x0040_0000u64;
    l.map(&MapRequest {
        asid: a,
        va,
        pa: Some(pa),
        page: PageSize::Large,
        permissions: RWX,
        global: false,
        invalidate: true,
    })
    .unwrap();

    let mut reference = RefModel::new();
    reference.map_large(a, va, pa, ref_perms(RWX), false);

    for &probe in &[0x0080_0000u64, 0x0080_1234, 0x00BF_FFF0] {
        let want = reference.translate(a, probe, RefAccess::Read).unwrap();
        match translate(&mut l, a, probe, 1, AccessKind::Read) {
            TranslateOutcome::Ok(r) => {
                assert_eq!(r.pa, want.pa);
                assert_eq!(r.page, PageSize::Large);
                assert_eq!(r.segments.len(), 1);
            }
            other => panic!("{other:?}"),
        }
    }

    // 长度 8 KiB 且完全在大页内：单段覆盖，PA 连续。
    match l
        .translate(&TranslateRequest {
            asid: a,
            va: 0x0080_1000,
            access: AccessKind::Read,
            len: 8192,
            fetch_pte: false,
        })
        .unwrap()
    {
        TranslateOutcome::Ok(r) => {
            assert_eq!(r.segments.len(), 1);
            assert_eq!(r.segments[0].bytes, 8192);
            assert_eq!(r.pa, 0x0040_1000);
        }
        other => panic!("{other:?}"),
    }
}

#[test]
fn global_mapping_is_visible_in_all_spaces_including_future_ones() {
    let mut l = lab(4096, 16);
    let p1 = l.create_asid(None).unwrap().0;
    let p2 = l.create_asid(None).unwrap().0;
    let pa = l
        .map(&MapRequest {
            asid: p1,
            va: 0x0,
            pa: Some(REF_LARGE),
            page: PageSize::Large,
            permissions: ROX,
            global: true,
            invalidate: true,
        })
        .unwrap()
        .pa;

    // 已存在的 p2 立即可见。
    match translate(&mut l, p2, 0x1234, 1, AccessKind::Read) {
        TranslateOutcome::Ok(r) => assert_eq!(r.pa, pa + 0x1234),
        other => panic!("{other:?}"),
    }

    // 之后创建的 p3 也必须复刻全局映射。
    let p3 = l.create_asid(None).unwrap().0;
    match translate(&mut l, p3, 0x1234, 1, AccessKind::Fetch) {
        TranslateOutcome::Ok(r) => {
            assert_eq!(r.pa, pa + 0x1234);
            assert!(r.executable && !r.writable);
        }
        other => panic!("{other:?}"),
    }

    // 全局 TLB 条目跨 ASID 命中：p2 翻译填充后，p3 同 VA 命中同一条目。
    match translate(&mut l, p3, 0x2000, 1, AccessKind::Read) {
        TranslateOutcome::Ok(r) => assert_eq!(r.tlb_segments, 1),
        other => panic!("{other:?}"),
    }
}

// ---------------------------------------------------------------------------
// 资源耗尽与错误分类
// ---------------------------------------------------------------------------

#[test]
fn frame_exhaustion_is_reported_as_resource_error_with_counts() {
    // 3 帧：根表 1 + 首次映射（L2 表 1 + 数据 1）= 3 全部用完。
    let mut l = lab(3, 4);
    let a = l.create_asid(None).unwrap().0;
    map_small(&mut l, a, 0x1000, RWX, false);
    assert_eq!(l.frames().available(), 0);

    // 不同 L1 区的第二次映射需要新 L2 表，必耗尽。
    let err = l
        .map(&MapRequest {
            asid: a,
            va: 0x0080_0000,
            pa: None,
            page: PageSize::Small,
            permissions: RWX,
            global: false,
            invalidate: true,
        })
        .unwrap_err();
    match err {
        DomainError::ResourceExhausted(r) => match r {
            mmu_lab::error::ResourceKind::Frames {
                requested,
                available,
            } => {
                assert!(requested >= 1);
                assert_eq!(available, 0);
            }
            other => panic!("期望帧耗尽，实际 {other:?}"),
        },
        other => panic!("期望资源耗尽类别，实际 {other:?}"),
    }
}

#[test]
fn invalid_requests_are_input_errors_and_computation_category_is_distinct() {
    let mut l = lab(256, 16);
    let a = l.create_asid(None).unwrap().0;

    let bad_align = l
        .map(&MapRequest {
            asid: a,
            va: 0x1234,
            pa: None,
            page: PageSize::Small,
            permissions: RWX,
            global: false,
            invalidate: true,
        })
        .unwrap_err();
    assert_eq!(bad_align.category(), "input_error");

    let unknown = l
        .translate(&TranslateRequest {
            asid: 99,
            va: 0x1000,
            access: AccessKind::Read,
            len: 1,
            fetch_pte: false,
        })
        .unwrap_err();
    assert_eq!(unknown.category(), "state_conflict");

    let too_long = l
        .translate(&TranslateRequest {
            asid: a,
            va: 0,
            access: AccessKind::Read,
            len: REF_LARGE + 1,
            fetch_pte: false,
        })
        .unwrap_err();
    assert_eq!(too_long.category(), "input_error");

    // 计算失败类别（帧记账层面的非法释放）必须与输入错误可区分。
    let mut fr = mmu_lab::frames::FrameAllocator::new(2);
    let comp = fr.free(0x1_0000, 1).unwrap_err();
    assert_eq!(comp.category(), "computation_failure");
}

#[test]
fn unmap_returns_frames_and_subsequent_translation_faults() {
    let mut l = lab(256, 16);
    let a = l.create_asid(None).unwrap().0;
    let va = 0x1000;
    map_small(&mut l, a, va, RWX, false);
    let avail = l.frames().available();
    l.unmap(a, va, false, true).unwrap();
    assert!(
        l.frames().available() > avail,
        "unmap 应归还数据帧与空 L2 表帧"
    );
    match translate(&mut l, a, va, 1, AccessKind::Read) {
        TranslateOutcome::Fault(f) => {
            assert!(matches!(f.kind, FaultKind::NotPresent { level: 1, .. }))
        }
        other => panic!("unmap 后应 L1 缺页：{other:?}"),
    }
}
