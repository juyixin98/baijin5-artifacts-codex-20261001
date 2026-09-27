"""精化主流程测试：良态收敛、病态升级、奇异识别、未达标与批量右端。"""
from dataclasses import replace

import pytest

from irsolver.reference import reference_backward_error, reference_solve
from irsolver.refinement import CONVERGED, NOT_CONVERGED, SINGULAR, solve_system

from tests.fixtures import (
    direct_solve,
    forward_rel_error,
    make_batch_system,
    make_hilbert_system,
    make_random_system,
    make_singular_system,
)


@pytest.fixture(scope="module")
def well():
    return make_random_system(n=8, kappa=1e2, seed=11, name="well")


@pytest.fixture(scope="module")
def ill():
    return make_random_system(n=8, kappa=1e10, seed=23, name="ill")


def test_well_conditioned_converges_at_fp32(well, settings):
    report = solve_system(well.A, well.B, settings)
    assert report.status == CONVERGED
    col = report.columns[0]
    assert col.status == CONVERGED
    assert col.tier_used == "fp32"
    assert col.eta_componentwise <= settings.tolerance
    # 防误报：独立参考模块重算 η，必须同样达标
    assert reference_backward_error(well.A, col.solution, well.B, 0) <= settings.tolerance
    # 前向误差：精化解应远优于直接 fp32 解
    err_refined = forward_rel_error(col.solution, well.x_true[0])
    err_fp32 = forward_rel_error(direct_solve(well.A, well.B, 0, "float32"), well.x_true[0])
    assert err_refined <= 1e-12
    assert err_refined < err_fp32 / 1e3
    # η 逐迭代改善的证据
    etas = [r.eta_componentwise for r in col.trace]
    assert etas[-1] < etas[0]


def test_reference_solution_matches_constructed_truth(well, settings):
    """夹具构造的精确解与独立 Householder QR 参考解一致（双重对照）。"""
    x_ref = reference_solve(well.A, well.B, settings.reference_dps)[0]
    err = forward_rel_error([float(v) for v in x_ref], well.x_true[0])
    assert err <= 1e-30


def test_ill_conditioned_escalates_to_fp64(ill, settings):
    """κ≈1e10：fp32 阶段校正无改善必须升级，不允许停滞在 fp32 报收敛。"""
    report = solve_system(ill.A, ill.B, settings)
    col = report.columns[0]
    assert col.status == CONVERGED
    assert col.tier_used == "fp64"
    escalations = [e for e in report.journal if e["event"] == "escalate"]
    assert escalations and escalations[0]["from_tier"] == "fp32"
    assert reference_backward_error(ill.A, col.solution, ill.B, 0) <= settings.tolerance
    err_refined = forward_rel_error(col.solution, ill.x_true[0])
    err_fp32 = forward_rel_error(direct_solve(ill.A, ill.B, 0, "float32"), ill.x_true[0])
    assert err_fp32 > 1e-2  # 直接 fp32 已明显不可信
    assert err_refined < 1e-4  # 精化后恢复到可用精度
    assert err_refined < err_fp32 / 1e2


def test_hilbert_converged_but_forward_accuracy_limited(settings):
    """Hilbert-12（κ≈1.6e16）：后向误差可达标，但前向误差界必须如实报告 > 1。"""
    fx = make_hilbert_system(12)
    report = solve_system(fx.A, fx.B, settings)
    col = report.columns[0]
    assert col.status == CONVERGED
    assert col.forward_error_bound is not None and col.forward_error_bound > 1.0
    assert "κ·η" in report.accuracy_note


def test_singular_matrix_detected_and_explained(settings):
    fx = make_singular_system(n=8, seed=5)
    report = solve_system(fx.A, fx.B, settings)
    assert report.status == SINGULAR
    assert report.condition.rank_deficient is True
    assert report.condition.rank == 7
    for col in report.columns:
        assert col.status == SINGULAR
        assert col.solution is None
        assert "奇异" in col.message
    assert any(e["event"] == "singular" for e in report.journal)


def test_not_converged_when_tolerance_below_all_floors(well, settings):
    """容差低于所有精度级的可达后向误差下限时，必须返回未达标而非误报。"""
    tight = replace(settings, tolerance=1e-35, mp_dps=30)
    report = solve_system(well.A, well.B, tight)
    col = report.columns[0]
    assert report.status == "failed"
    assert col.status == NOT_CONVERGED
    assert col.solution is None
    assert col.tier_used == "mpmath"  # 已升到最高级
    tiers_seen = {r.tier for r in col.trace}
    assert tiers_seen == {"fp32", "fp64", "mpmath"}
    assert "未达标" in col.message
    assert not any(e["event"] == "converged" for e in report.journal)


def test_tight_tolerance_reaches_mpmath_tier(well, settings):
    """容差 1e-20 低于 fp32/fp64 级残差下限，应由 mpmath 级达成。"""
    tight = replace(settings, tolerance=1e-20)
    report = solve_system(well.A, well.B, tight)
    col = report.columns[0]
    assert col.status == CONVERGED
    assert col.tier_used == "mpmath"
    assert col.eta_componentwise <= 1e-20


def test_batch_columns_reported_independently(settings):
    fx = make_batch_system(n=8, kappa=1e6, seed=99)
    report = solve_system(fx.A, fx.B, settings)
    assert report.status == CONVERGED
    assert len(report.columns) == 3
    for j, col in enumerate(report.columns):
        assert col.index == j
        assert col.status == CONVERGED
        assert col.iterations > 0 and len(col.trace) == col.iterations
        assert col.eta_componentwise <= settings.tolerance
        err = forward_rel_error(col.solution, fx.x_true[j])
        # 前向误差受 κ·η 限制（κ≈1e6，η≤1e-10），1e-5 是该量级下的达标线
        assert err <= 1e-5, f"第 {j} 列前向误差超标: {err:.2e}"
    # 各列迭代轨迹独立记录（对象互不相同，内容各自完整）
    assert len({id(c.trace) for c in report.columns}) == 3
    assert all(all(r.tier for r in c.trace) for c in report.columns)


def test_every_converged_claim_independently_verified(settings):
    """核心不变量：任何 converged 判定都必须经得起独立参考复核。"""
    for fx in [
        make_random_system(8, 1e2, 41, "v1"),
        make_random_system(8, 1e6, 42, "v2"),
        make_random_system(8, 1e10, 43, "v3"),
        make_random_system(8, 1e14, 44, "v4"),
    ]:
        report = solve_system(fx.A, fx.B, settings)
        col = report.columns[0]
        assert col.status == CONVERGED, fx.name
        eta_ref = reference_backward_error(fx.A, col.solution, fx.B, 0)
        assert eta_ref <= settings.tolerance, f"{fx.name}: 独立复核 η={eta_ref:.2e}"
