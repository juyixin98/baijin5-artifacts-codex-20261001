"""Unit tests for the local-linear estimation kernel.

Includes the contractual guarantee that the estimator is a local *linear*
projection, not the raw mean difference in a window (which is biased when
the conditional mean slopes near the cutoff).
"""
from __future__ import annotations

import numpy as np
import pytest

from app.contract import KernelName
from app.errors import SingularFitError
from app.estimator import LEFT, RIGHT, fit_side, jump


def _line(n: int = 200, slope: float = 4.0, seed: int = 0):
    rng = np.random.default_rng(seed)
    x = rng.uniform(-1, 1, n)
    y = slope * x  # continuous steep line, no jump
    return x, y


@pytest.mark.unit
def test_side_fit_recovers_intercept_and_slope_of_noisy_line() -> None:
    rng = np.random.default_rng(5)
    x = rng.uniform(-1, 1, 600)
    y = 2.0 + 3.0 * x + rng.normal(0, 0.05, x.size)
    res = fit_side(x, y, 0.0, 2.0, KernelName.TRIANGULAR, RIGHT, RIGHT)
    assert res.intercept == pytest.approx(2.0, abs=0.02)
    assert res.slope == pytest.approx(3.0, abs=0.05)
    assert res.n == int(np.sum((x >= 0) & (x <= 2.0)))
    assert 0 < res.effective_n <= res.n


@pytest.mark.unit
def test_local_linear_unbiased_under_slope_unlike_raw_mean_diff() -> None:
    """Contract: do not replace the local estimate by raw window mean diff.

    On y = 4x (continuous, true jump 0), the raw difference of within-window
    means is strongly positive because the right window sits at higher x.
    The local-linear jump must be ~ 0. This is the exact bias the contract
    forbids.
    """
    x, y = _line(slope=4.0, seed=1)
    h = 0.4
    left = fit_side(x, y, 0.0, h, KernelName.TRIANGULAR, LEFT, RIGHT)
    right = fit_side(x, y, 0.0, h, KernelName.TRIANGULAR, RIGHT, RIGHT)
    tau_ll = jump(left, right, treatment_above=True)

    raw_left = y[(x >= -h) & (x < 0)].mean()
    raw_right = y[(x >= 0) & (x <= h)].mean()
    raw_diff = raw_right - raw_left

    assert abs(raw_diff) > 0.5, "raw mean diff should be badly biased on a slope"
    assert abs(tau_ll) < 0.15, "local-linear jump must remove the slope bias"


@pytest.mark.unit
def test_two_sides_fit_independently_with_own_slopes() -> None:
    rng = np.random.default_rng(9)
    x = rng.uniform(-1, 1, 800)
    y = np.where(x >= 0, 5.0 + 0.5 * x, 1.0 + 3.0 * x) + rng.normal(0, 0.03, x.size)
    left = fit_side(x, y, 0.0, 1.0, KernelName.TRIANGULAR, LEFT, RIGHT)
    right = fit_side(x, y, 0.0, 1.0, KernelName.TRIANGULAR, RIGHT, RIGHT)
    assert left.intercept == pytest.approx(1.0, abs=0.05)
    assert right.intercept == pytest.approx(5.0, abs=0.05)
    assert left.slope == pytest.approx(3.0, abs=0.1)
    assert right.slope == pytest.approx(0.5, abs=0.1)
    assert jump(left, right, True) == pytest.approx(4.0, abs=0.1)


@pytest.mark.unit
def test_reversed_treatment_coding_flips_sign() -> None:
    rng = np.random.default_rng(3)
    x = rng.uniform(-1, 1, 500)
    y = np.where(x >= 0, 2.0, 0.0) + rng.normal(0, 0.05, x.size)
    left = fit_side(x, y, 0.0, 1.0, KernelName.TRIANGULAR, LEFT, RIGHT)
    right = fit_side(x, y, 0.0, 1.0, KernelName.TRIANGULAR, RIGHT, RIGHT)
    assert jump(left, right, True) == pytest.approx(2.0, abs=0.1)
    assert jump(left, right, False) == pytest.approx(-2.0, abs=0.1)


@pytest.mark.unit
def test_singular_when_too_few_points() -> None:
    x = np.array([0.1, 0.2])
    y = np.array([1.0, 2.0])
    with pytest.raises(SingularFitError):
        fit_side(x, y, 0.0, 1.0, KernelName.UNIFORM, RIGHT, RIGHT)


@pytest.mark.unit
def test_cutoff_mass_assigned_to_treated_side_by_default() -> None:
    x = np.array([-0.6, -0.5, -0.2, 0.0, 0.0, 0.2, 0.5])
    y = np.array([0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0])
    right = fit_side(x, y, 0.0, 1.0, KernelName.UNIFORM, RIGHT, RIGHT)
    left = fit_side(x, y, 0.0, 1.0, KernelName.UNIFORM, LEFT, RIGHT)
    # both cutoff points land on the right under x >= c
    assert right.n == 4
    assert left.n == 3
