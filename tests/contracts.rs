//! 行为契约夹具（一）：
//! 同虚址不同进程、跨页写权限拆分、权限降级、TLB 过期、大小页冲突、故障类别。
//!
//! 期望值由 `common::Oracle`（独立区间预言机）与手算常量给出，
//! 不复用被测实现的遍历逻辑。

mod common;

use common::{Oracle, OracleMapping};
use mmu_teach::config::PAGE_SIZE;
use mmu_teach::errors::FaultKind;
use mmu_teach::machine::MachineTranslateError;
use mmu_teach::pte::{encode_leaf, AccessOp, Permissions, BIT_V, BIT_W};
use mmu_teach::{Machine, RunLog};

fn rwx() -> Permissions {
    Permissions {
        read: true,
        write: true,
        execute: true,
        user: true,
        global: false,
    }
}
fn rox() -> Permissions {
    Permissions {
        read: true,
        write: false,
        execute: true,
        user: true,
        global: false,
    }
}
fn machine() -> Machine {
    Machine::new(4096, RunLog::new())
}

fn fault_kind(err: MachineTranslateError) -> FaultKind {
    match err {
        MachineTranslateError::Fault(te) => te.fault.kind,
        MachineTranslateError::Input(e) => panic!("期望页故障，得到输入/服务错误: {e}"),
    }
}

// ---------------------------------------------------------------------------
// 1) 同一虚拟地址、不同进程 → 翻译到不同物理地址（ASID 隔离）
// ---------------------------------------------------------------------------

#[test]
fn same_vaddr_in_two_processes_translates_to_distinct_paddrs() {
    let m = machine();
    let p1 = m.create_process("proc-a".into()).unwrap().asid;
    let p2 = m.create_process("proc-b".into()).unwrap().asid;

    let vaddr = 0x0000_0000_0000_4000u64;
    // 两个进程把同一 VA 映射到不同 PPN。
    m.map(p1, vaddr, 3, 100, rwx()).unwrap();
    m.map(p2, vaddr, 3, 200, rwx()).unwrap();

    let truth_p1 = [OracleMapping {
        va: vaddr,
        size: PAGE_SIZE,
        ppn: 100,
        perms: rwx(),
    }];
    let oracle = Oracle::new(&truth_p1);

    let t1 = m.translate(p1, vaddr, AccessOp::Read).unwrap();
    let t2 = m.translate(p2, vaddr, AccessOp::Read).unwrap();

    // 与独立预言机复算的物理地址逐一核对。
    assert_eq!(t1.paddr, oracle.translate(vaddr).unwrap().0);
    // 进程 2 用第二张映射的预言机值（这里直接手算第二组）。
    assert_eq!(t2.paddr, 200 * PAGE_SIZE);
    assert_ne!(t1.paddr, t2.paddr, "ASID 隔离失败：同虚址得到同物理地址");
    assert_eq!(t1.ppn, 100);
    assert_eq!(t2.ppn, 200);
    // 第二次翻译进程 1 应命中 TLB，且拿到的仍是进程 1 的 PPN（不串号）。
    let t1b = m.translate(p1, vaddr + 0x10, AccessOp::Read).unwrap();
    assert_eq!(t1b.ppn, 100, "TLB 命中后 ASID 串号");
}

// ---------------------------------------------------------------------------
// 2) 跨页写：两片分别翻译、分别验证权限
// ---------------------------------------------------------------------------

#[test]
fn cross_page_write_succeeds_with_per_page_translation() {
    let m = machine();
    let a = m.create_process("cross".into()).unwrap().asid;
    m.map(a, 0x0000, 3, 100, rwx()).unwrap();
    m.map(a, 0x1000, 3, 200, rwx()).unwrap();

    let truth = [
        OracleMapping {
            va: 0x0000,
            size: PAGE_SIZE,
            ppn: 100,
            perms: rwx(),
        },
        OracleMapping {
            va: 0x1000,
            size: PAGE_SIZE,
            ppn: 200,
            perms: rwx(),
        },
    ];
    let oracle = Oracle::new(&truth);

    // 从 0x0ff8 写 16 字节，跨 0x1000 边界，各 8 字节。
    let report = m.access(a, 0x0ff8, 16, AccessOp::Write).unwrap();
    assert!(report.success, "跨页写应成功: {report:?}");
    assert!(report.crossed_page);
    assert_eq!(report.pieces.len(), 2);

    let starts = [0x0ff8u64, 0x1000];
    for (i, piece) in report.pieces.iter().enumerate() {
        match piece {
            mmu_teach::mmu::PieceResult::Ok {
                vaddr,
                len,
                paddr_start,
                op,
                ..
            } => {
                assert_eq!(*vaddr, starts[i]);
                assert_eq!(*len, 8);
                assert_eq!(*op, AccessOp::Write);
                assert_eq!(*paddr_start, oracle.translate(*vaddr).unwrap().0);
            }
            other => panic!("片段 {i} 应成功: {other:?}"),
        }
    }
}

#[test]
fn cross_page_write_fails_second_page_when_permission_downgraded() {
    let m = machine();
    let a = m.create_process("cross-prot".into()).unwrap().asid;
    m.map(a, 0x0000, 3, 100, rwx()).unwrap();
    m.map(a, 0x1000, 3, 200, rwx()).unwrap();

    // 先做一次跨页写以填满两片 TLB。
    assert!(m.access(a, 0x0ff8, 16, AccessOp::Write).unwrap().success);

    // 把第二页降为只读——reprotect 自动失效对应 VPN。
    m.reprotect(a, 0x1000, rox()).unwrap();

    let report = m.access(a, 0x0ff8, 16, AccessOp::Write).unwrap();
    assert!(!report.success, "权限降级后跨页写必须失败");
    // 第一片成功、第二片权限拒绝，中间状态被保留。
    assert!(matches!(
        report.pieces[0],
        mmu_teach::mmu::PieceResult::Ok { .. }
    ));
    match &report.pieces[1] {
        mmu_teach::mmu::PieceResult::Fault { kind, op, .. } => {
            assert_eq!(kind, "permission_denied");
            assert_eq!(*op, AccessOp::Write);
        }
        other => panic!("第二片应为权限故障: {other:?}"),
    }
}

// ---------------------------------------------------------------------------
// 3) 权限降级：常规路径自动刷 TLB
// ---------------------------------------------------------------------------

#[test]
fn permission_downgrade_is_visible_after_automatic_flush() {
    let m = machine();
    let a = m.create_process("prot".into()).unwrap().asid;
    m.map(a, 0x5000, 3, 400, rwx()).unwrap();

    // 第一次写：走页表并把 W 权限填入 TLB。
    let t = m.translate(a, 0x5000, AccessOp::Write).unwrap();
    assert_eq!(t.paddr, 400 * PAGE_SIZE);

    // 降级为只读：失效协议自动刷该叶子 VPN。
    let (rp, flush) = m.reprotect(a, 0x5000, rox()).unwrap();
    assert_ne!(rp.old_pte, rp.new_pte);
    assert!(flush.entries_removed >= 1, "降级必须刷掉陈旧 W 表项");

    // 再写：走页表（TLB 已失效），得到权限拒绝。
    let err = m.translate(a, 0x5000, AccessOp::Write).unwrap_err();
    assert_eq!(fault_kind(err), FaultKind::PermissionDenied);
    // 读仍然允许。
    assert!(m.translate(a, 0x5000, AccessOp::Read).is_ok());
}

// ---------------------------------------------------------------------------
// 4) TLB 过期夹具：绕过失效协议直接改 PTE
// ---------------------------------------------------------------------------

#[test]
fn stale_tlb_serves_old_permission_until_sfence() {
    let m = machine();
    let a = m.create_process("stale".into()).unwrap().asid;
    m.map(a, 0x4000, 3, 300, rwx()).unwrap();

    // 预热 TLB（缓存 W=1）。
    assert!(m.translate(a, 0x4000, AccessOp::Write).is_ok());

    // 夹具专用：直接把末级 PTE 改成只读，且【故意不刷 TLB】。
    let stale_pte = encode_leaf(300, &rox());
    m.raw_write_pte(a, 0x4000, 3, stale_pte).unwrap();

    // 此刻 TLB 仍持有陈旧 W：翻译走 tlb_hit，写“错误地”被放行——暴露过期表项。
    let stale = m.translate(a, 0x4004, AccessOp::Write).unwrap();
    assert_eq!(
        stale.source,
        mmu_teach::mmu::TranslateSource::TlbHit,
        "应命中陈旧 TLB 项"
    );

    // 执行显式失效后，再次翻译走页表并读到新权限 → 写被拒绝。
    let flushed = m.sfence_vma(Some(a), Some(0x4000u64 >> 12));
    assert!(flushed.entries_removed >= 1);
    let err = m.translate(a, 0x4004, AccessOp::Write).unwrap_err();
    assert_eq!(fault_kind(err), FaultKind::PermissionDenied);
}

// ---------------------------------------------------------------------------
// 5) 大页/小页覆盖冲突：两个方向都拒绝
// ---------------------------------------------------------------------------

#[test]
fn superpage_and_basepage_overlap_rejected_both_directions() {
    // 方向 A：先 2MiB 大页，再在其内部建 4KiB。
    let m = machine();
    let a = m.create_process("big-first".into()).unwrap().asid;
    m.map(a, 0x20_0000, 2, 512, rwx()).unwrap(); // PPN 必须 512 对齐
    let err = m.map(a, 0x20_1000, 3, 1000, rwx()).unwrap_err();
    assert_eq!(
        err.kind(),
        "conflict",
        "大页覆盖小页应是状态冲突，得到 {err:?}"
    );

    // 方向 B：先 4KiB 小页，再建覆盖它的 2MiB。
    let b = m.create_process("small-first".into()).unwrap().asid;
    m.map(b, 0x1000, 3, 100, rwx()).unwrap();
    let err = m.map(b, 0x0000, 2, 512, rwx()).unwrap_err();
    assert_eq!(
        err.kind(),
        "conflict",
        "小页区域上建大页应被拒绝，得到 {err:?}"
    );
}

// ---------------------------------------------------------------------------
// 6) 页故障类别区分（不是 panic）
// ---------------------------------------------------------------------------

#[test]
fn fault_categories_are_distinguished() {
    let m = machine();
    let a = m.create_process("faults".into()).unwrap().asid;

    // 6.1 空地址空间：根表即中断 → miss。
    let err = m.translate(a, 0x1234_5678, AccessOp::Read).unwrap_err();
    assert_eq!(fault_kind(err), FaultKind::Miss);

    // 6.2 建一个 4KiB 页后，访问同一 L3 表中的相邻空槽 → page_not_present。
    m.map(a, 0x6000, 3, 500, rwx()).unwrap();
    let err = m.translate(a, 0x7000, AccessOp::Read).unwrap_err();
    assert_eq!(fault_kind(err), FaultKind::PageNotPresent);

    // 6.3 写入非法 PTE（W=1,R=0）→ reserved_fault。
    m.map(a, 0x8000, 3, 600, rwx()).unwrap();
    m.raw_write_pte(a, 0x8000, 3, BIT_V | BIT_W).unwrap();
    m.sfence_vma(Some(a), Some(0x8000u64 >> 12));
    let err = m.translate(a, 0x8000, AccessOp::Read).unwrap_err();
    assert_eq!(fault_kind(err), FaultKind::ReservedFault);

    // 6.4 非规范地址（bit48 置位）→ 输入错误，而非页故障。
    let err = m.translate(a, 1u64 << 48, AccessOp::Read).unwrap_err();
    match err {
        MachineTranslateError::Input(e) => assert_eq!(e.kind(), "input_error"),
        MachineTranslateError::Fault(f) => panic!("应为输入错误，得到页故障 {:?}", f.fault),
    }
}

// ---------------------------------------------------------------------------
// 7) 大页物理地址由独立预言机复算
// ---------------------------------------------------------------------------

#[test]
fn superpage_physical_address_matches_oracle() {
    let m = machine();
    let a = m.create_process("super".into()).unwrap().asid;
    m.map(a, 0x20_0000, 2, 512, rwx()).unwrap(); // 2MiB @ PPN512

    let truth = [OracleMapping {
        va: 0x20_0000,
        size: 2 * 1024 * 1024,
        ppn: 512,
        perms: rwx(),
    }];
    let oracle = Oracle::new(&truth);

    for off in [0u64, 0xabc, 0xf_ffff] {
        let va = 0x20_0000 + off;
        let t = m.translate(a, va, AccessOp::Read).unwrap();
        assert_eq!(t.paddr, oracle.translate(va).unwrap().0, "offset {off:#x}");
        assert_eq!(t.leaf_level, 2);
    }
    // 写/执行权限位也独立核验。
    assert!(m.translate(a, 0x20_0000, AccessOp::Write).is_ok());
    assert!(m.translate(a, 0x20_0000, AccessOp::Execute).is_ok());
}

// ---------------------------------------------------------------------------
// 8) 资源耗尽是独立错误类别
// ---------------------------------------------------------------------------

#[test]
fn frame_exhaustion_is_reported_as_resource_error() {
    // 3 帧：根表占 1，建一个 4KiB 映射还需 3 张页表 → 不足。
    let m = Machine::new(3, RunLog::new());
    let a = m.create_process("tiny".into()).unwrap().asid;
    let err = m.map(a, 0x9000, 3, 100, rwx()).unwrap_err();
    assert_eq!(err.kind(), "resource_exhausted");
}

// ---------------------------------------------------------------------------
// 9) unmap 级联回收空页表，帧可被复用
// ---------------------------------------------------------------------------

#[test]
fn unmap_reclaims_empty_tables_and_translation_faults() {
    let m = machine();
    let a = m.create_process("unmap".into()).unwrap().asid;
    m.map(a, 0x1234_5000, 3, 700, rwx()).unwrap();
    assert!(m.translate(a, 0x1234_5000, AccessOp::Read).is_ok());

    let used_before = m.info().frame_pool_used;
    let (unmap_report, _flush) = m.unmap(a, 0x1234_5000, 3).unwrap();
    // 该路径上 L1/L2/L3 三张表在唯一映射拆除后应变空并回收（根表保留）。
    assert!(unmap_report.reclaimed_tables >= 1, "应至少回收空页表");
    let used_after = m.info().frame_pool_used;
    assert!(used_after < used_before, "回收后帧占用应下降");

    // 再翻译得到故障（末级页表已不存在 -> miss）。
    let err = m.translate(a, 0x1234_5000, AccessOp::Read).unwrap_err();
    assert_eq!(fault_kind(err), FaultKind::Miss);

    // 重复 unmap 是状态冲突，而不是崩溃。
    let err = m.unmap(a, 0x1234_5000, 3).unwrap_err();
    assert_eq!(err.kind(), "conflict");
}

// ---------------------------------------------------------------------------
// 10) 执行权限独立判定：不可执行页上 Execute 被拒
// ---------------------------------------------------------------------------

#[test]
fn execute_permission_is_checked_independently() {
    let m = machine();
    let a = m.create_process("noexec".into()).unwrap().asid;
    // 只给 R/W，不给 X。
    let rw = Permissions {
        read: true,
        write: true,
        execute: false,
        user: true,
        global: false,
    };
    m.map(a, 0xa000, 3, 800, rw).unwrap();
    assert!(m.translate(a, 0xa000, AccessOp::Read).is_ok());
    assert!(m.translate(a, 0xa000, AccessOp::Write).is_ok());
    let err = m.translate(a, 0xa000, AccessOp::Execute).unwrap_err();
    assert_eq!(fault_kind(err), FaultKind::PermissionDenied);
}

// ---------------------------------------------------------------------------
// 11) 全局映射（G=1）跨 ASID 可见
// ---------------------------------------------------------------------------

#[test]
fn global_mapping_is_visible_across_asids() {
    let m = machine();
    let p1 = m.create_process("g1".into()).unwrap().asid;
    let p2 = m.create_process("g2".into()).unwrap().asid;
    let global = Permissions {
        read: true,
        write: false,
        execute: true,
        user: true,
        global: true,
    };
    // 内核全局页：在 p1 的根下建立 G=1 映射。
    m.map(p1, 0xffff_ff00_0000, 3, 900, global).unwrap();
    // p2 没有自己的映射，但全局项对其可见（TLB 层语义）。
    let _ = m.translate(p1, 0xffff_ff00_0010, AccessOp::Read).unwrap();
    // 通过 TLB 命中验证跨 ASID 可见性。
    let t2 = m.translate(p2, 0xffff_ff00_0010, AccessOp::Read).unwrap();
    assert_eq!(t2.ppn, 900);
    assert_eq!(t2.source, mmu_teach::mmu::TranslateSource::TlbHit);
}

// ---------------------------------------------------------------------------
// 12) 1 GiB 大页（L1）翻译与对齐校验
// ---------------------------------------------------------------------------

#[test]
fn one_gib_page_translates_and_requires_alignment() {
    let m = machine();
    let a = m.create_process("gib".into()).unwrap().asid;
    // PPN 需按 1GiB（262144 帧）对齐：取 PPN=262144。
    const GIB_PPN: u64 = 262_144;
    m.map(a, 0x0000_0000, 1, GIB_PPN, rwx()).unwrap();
    let va = 0x1000_0000u64 - 1; // 落在第一个 1GiB 区域末端
    let t = m.translate(a, va, AccessOp::Read).unwrap();
    assert_eq!(t.leaf_level, 1);
    assert_eq!(t.paddr, GIB_PPN * 4096 + va);

    // 未按 1GiB 对齐的虚址 -> input_error。
    let err = m.map(a, 0x0000_1000, 1, GIB_PPN * 2, rwx()).unwrap_err();
    assert_eq!(err.kind(), "input_error");
}
