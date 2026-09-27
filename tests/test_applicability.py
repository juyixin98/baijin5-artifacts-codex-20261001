"""适用范围验证：精化 vs 直接低精度，随条件数变化的对照扫描。

扫描 κ ∈ {1e2, 1e6, 1e10, 1e14, 1e16}，对同一系统比较：
- 直接 fp32 求解（对照组）
- 直接 fp64 求解（对照组）
- 本求解器迭代精化（被测对象）
前向误差以夹具构造的精确解为准，并把证据表写入 reports/applicability.md。
"""
from pathlib import Path

import pytest

from irsolver.refinement import CONVERGED, solve_system

from tests.fixtures import (
    direct_solve,
    forward_rel_error,
    make_random_system,
)

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "reports" / "applicability.md"

SWEEP_KAPPAS = [1e2, 1e6, 1e10, 1e14, 1e16]


def _solve_row(settings, kappa, seed):
    fx = make_random_system(n=8, kappa=kappa, seed=seed, name=f"k{kappa:.0e}")
    report = solve_system(fx.A, fx.B, settings)
    col = report.columns[0]
    err_refined = (
        forward_rel_error(col.solution, fx.x_true[0])
        if col.solution is not None
        else float("inf")
    )
    err_fp32 = forward_rel_error(direct_solve(fx.A, fx.B, 0, "float32"), fx.x_true[0])
    err_fp64 = forward_rel_error(direct_solve(fx.A, fx.B, 0, "float64"), fx.x_true[0])
    return {
        "kappa": kappa,
        "kappa_est": report.condition.kappa,
        "status": col.status,
        "tier": col.tier_used,
        "iters": col.iterations,
        "eta": col.eta_componentwise,
        "err_refined": err_refined,
        "err_fp32": err_fp32,
        "err_fp64": err_fp64,
        "fwd_bound": col.forward_error_bound,
    }


@pytest.fixture(scope="module")
def sweep(settings):
    return [_solve_row(settings, k, 1000 + i) for i, k in enumerate(SWEEP_KAPPAS)]


def _row(rows, kappa):
    return next(r for r in rows if r["kappa"] == kappa)


def test_refinement_beats_fp32_when_moderately_conditioned(sweep, settings):
    """κ ≤ 1e6：fp32 级精化即收敛，前向误差比直接 fp32 低至少 3 个数量级。"""
    forward_bounds = {1e2: 1e-10, 1e6: 1e-6}  # 与 κ·η 量级一致的达标线
    for kappa in (1e2, 1e6):
        row = _row(sweep, kappa)
        assert row["status"] == CONVERGED
        assert row["tier"] == "fp32"
        assert row["eta"] <= settings.tolerance
        assert row["err_refined"] <= forward_bounds[kappa]
        assert row["err_refined"] < row["err_fp32"] / 1e3


def test_refinement_rescues_where_direct_fp32_fails(sweep):
    """κ = 1e10：直接 fp32 完全失效（误差 > 100%），精化升级后仍可用。"""
    row = _row(sweep, 1e10)
    assert row["status"] == CONVERGED
    assert row["tier"] == "fp64"
    assert row["err_fp32"] > 1.0
    assert row["err_refined"] < 1e-4


def test_refinement_matches_fp64_quality_at_high_kappa(sweep):
    """κ = 1e14：精化收敛且前向误差与直接 fp64 同量级，远优于 fp32。"""
    row = _row(sweep, 1e14)
    assert row["status"] == CONVERGED
    assert row["tier"] == "fp64"
    assert row["err_refined"] < 1.0
    assert row["err_refined"] < row["err_fp32"] / 1e3
    assert row["err_refined"] <= max(row["err_fp64"] * 10, 1e-14)


def test_beyond_fp64_range_backward_ok_forward_limited(sweep):
    """κ = 1e16：后向误差仍可达标，但前向误差界 > 1 必须如实暴露。"""
    row = _row(sweep, 1e16)
    assert row["status"] == CONVERGED
    assert row["tier"] in ("fp64", "mpmath")
    assert row["fwd_bound"] is not None and row["fwd_bound"] > 1.0
    # 精化仍不比直接 fp32 差
    assert row["err_refined"] <= row["err_fp32"]


def test_applicability_report_written(sweep, settings):
    """把对照证据写入 reports/applicability.md 供验收查阅。"""
    lines = [
        "# 精化 vs 直接低精度：适用范围对照证据",
        "",
        f"收敛容差 η ≤ {settings.tolerance:.1e}；前向误差为相对精确解的 ‖·‖∞ 相对误差。",
        "",
        "| κ(目标) | κ(估计) | 状态 | 精度级 | 迭代数 | η | 精化误差 | 直接fp32误差 | 直接fp64误差 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sweep:
        lines.append(
            f"| {r['kappa']:.0e} | {r['kappa_est']:.2e} | {r['status']} | {r['tier']} "
            f"| {r['iters']} | {r['eta']:.2e} | {r['err_refined']:.2e} "
            f"| {r['err_fp32']:.2e} | {r['err_fp64']:.2e} |"
        )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    text = REPORT_PATH.read_text(encoding="utf-8")
    assert "直接fp32误差" in text and text.count("converged") == len(SWEEP_KAPPAS)
