"""Levinson-Durbin: hand-computed recursion, Toeplitz cross-check, diagnostics."""
from __future__ import annotations

import numpy as np

from app.lpc.levinson import levinson_durbin
from app.lpc.toeplitz_ref import solve_normal_equations_toeplitz
from app.lpc.windowing import apply_window
from app.lpc.autocorr import autocorrelation
from tests.conftest import load_fixture


def test_hand_computed_recursion():
    # r = [4, 2, 1]:
    #   step 1: lambda = r[1] = 2, k1 = -2/4 = -0.5, a = [1, -0.5], E = 4*(1-0.25) = 3
    #   step 2: lambda = r[2] + a1*r[1] = 1 - 1 = 0, k2 = 0, a = [1, -0.5, 0], E = 3
    res = levinson_durbin(np.array([4.0, 2.0, 1.0]), 2)
    np.testing.assert_allclose(res.coefficients, [1.0, -0.5, 0.0], atol=1e-15)
    np.testing.assert_allclose(res.reflection_coefficients, [-0.5, 0.0], atol=1e-15)
    assert res.prediction_error_energy == 3.0
    assert res.gain == np.sqrt(3.0)
    assert res.stable
    assert res.completed_order == 2
    assert res.diagnostics == []


def test_matches_independent_toeplitz_solve_on_ar_fixture():
    fixture = load_fixture("ar_process.json")
    x = apply_window(np.asarray(fixture["samples"]), "hann")
    order = 10
    r = autocorrelation(x, order)
    res = levinson_durbin(r, order)
    reference = solve_normal_equations_toeplitz(r, order)
    deviation = np.max(np.abs(res.coefficients - reference))
    assert deviation < 1e-9, f"Levinson deviates from Toeplitz solve by {deviation}"


def test_zero_energy_frame_has_defined_behaviour():
    res = levinson_durbin(np.zeros(11), 10)
    np.testing.assert_array_equal(res.coefficients, [1.0] + [0.0] * 10)
    assert res.gain == 0.0
    assert res.prediction_error_energy == 0.0
    assert res.reflection_coefficients == []
    assert res.stable  # degenerate all-pole filter is the identity
    codes = [d.code for d in res.diagnostics]
    assert codes == ["ZERO_ENERGY_FRAME"]
    assert res.diagnostics[0].severity == "info"


def test_reflection_coefficient_instability_is_diagnosed():
    # r = [1, 1] is not a valid autocorrelation (|r1| > r0 is impossible for
    # real data); the recursion must surface k1 = -1 as unstable.
    res = levinson_durbin(np.array([1.0, 1.0]), 1)
    assert res.reflection_coefficients == [-1.0]
    assert not res.stable
    codes = [d.code for d in res.diagnostics]
    assert "REFLECTION_COEFFICIENT_UNSTABLE" in codes
    errors = [d for d in res.diagnostics if d.severity == "error"]
    assert errors, "instability must be reported as an error"


def test_error_energy_collapses_for_rank_deficient_autocorrelation():
    # r[k] = cos(w k) is the theoretical autocorrelation of a pure sinusoid:
    # rank 2, so any order above 2 is "too high" and the recursion must stop
    # with a collapse diagnostic instead of returning garbage.
    w = 2 * np.pi * 0.1
    r = np.cos(w * np.arange(8))
    res = levinson_durbin(r, 7)
    assert not res.stable
    assert res.completed_order == 2
    codes = [d.code for d in res.diagnostics]
    assert "PREDICTION_ERROR_ENERGY_COLLAPSED" in codes


def test_marginal_reflection_coefficient_is_a_warning_not_an_error():
    res = levinson_durbin(np.array([1.0, 0.9995]), 1)
    assert res.stable
    diag = res.diagnostics[0]
    assert diag.code == "REFLECTION_COEFFICIENT_MARGINAL"
    assert diag.severity == "warning"
