"""残差与误差证据测试：验证残差确实用原矩阵计算，判据与独立参考一致。"""
import numpy as np
import pytest

from irsolver import evidence
from irsolver.reference import reference_backward_error, reference_residual
from irsolver.residual import PreparedSystem, compute_residual

from tests.fixtures import make_random_system


@pytest.fixture(scope="module")
def fx():
    return make_random_system(n=8, kappa=1e2, seed=11, name="well")


def test_residual_uses_original_matrix_not_rounded(fx, settings):
    """核心残差必须贴近原矩阵高精度残差，而非 float32 舍入矩阵的残差。"""
    prep = PreparedSystem(fx.A, fx.B, settings.mp_dps)
    x = fx.x_true64(0) + 1e-6  # 非精确解，残差量级 ~1e-6
    r_core = compute_residual(prep, 0, x, "float64").as_float64
    r_exact = np.array([float(v) for v in reference_residual(fx.A, x, fx.B, 0)])
    a32_rounded = fx.A.to_float64().astype(np.float32).astype(np.float64)
    r_rounded = fx.B.to_float64()[:, 0] - a32_rounded @ x
    dist_exact = np.linalg.norm(r_core - r_exact, np.inf)
    dist_rounded = np.linalg.norm(r_core - r_rounded, np.inf)
    assert dist_exact < dist_rounded / 1e3


@pytest.mark.parametrize("kind", ["float64", "longdouble", "mpmath"])
def test_residual_matches_independent_reference(fx, settings, kind):
    """各精度级别的残差与独立 mpmath 参考残差一致。"""
    prep = PreparedSystem(fx.A, fx.B, settings.mp_dps)
    x = fx.x_true64(0) + 1e-6
    r = compute_residual(prep, 0, x, kind).as_float64
    r_exact = np.array([float(v) for v in reference_residual(fx.A, x, fx.B, 0)])
    assert np.linalg.norm(r - r_exact, np.inf) <= 1e-9 * np.linalg.norm(r_exact, np.inf)


def test_backward_error_near_zero_for_exact_solution(fx, settings):
    prep = PreparedSystem(fx.A, fx.B, settings.mp_dps)
    x = fx.x_true64(0)
    r = compute_residual(prep, 0, x, "float64")
    eta_comp, eta_norm = evidence.backward_errors(
        prep.A64, x, prep.operands("float64")[1][0], r.as_float64
    )
    assert eta_comp <= 1e-13
    assert eta_norm <= 1e-13


def test_backward_error_agrees_with_independent_reference(fx, settings):
    """核心判据与独立参考模块（不同代码路径）重算的 η 一致。"""
    prep = PreparedSystem(fx.A, fx.B, settings.mp_dps)
    rng = np.random.default_rng(3)
    x = fx.x_true64(0) + 1e-6 * rng.standard_normal(8)
    r = compute_residual(prep, 0, x, "float64")
    eta_core, _ = evidence.backward_errors(
        prep.A64, x, prep.operands("float64")[1][0], r.as_float64
    )
    eta_ref = reference_backward_error(fx.A, list(x), fx.B, 0)
    assert eta_core == pytest.approx(eta_ref, rel=1e-6)


def test_forward_error_bound_and_accuracy_note():
    assert evidence.forward_error_bound(1e10, 1e-12) == pytest.approx(1e-2)
    assert evidence.forward_error_bound(float("inf"), 1e-12) == float("inf")
    note = evidence.accuracy_note(1e10, "svd-float64", 1e-10)
    assert "1.000e+10" in note and "κ·η" in note
    assert "奇异" in evidence.accuracy_note(float("inf"), "svd-float64", 1e-10)
