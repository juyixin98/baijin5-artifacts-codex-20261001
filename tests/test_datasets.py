"""Tests for the synthetic DGP fixtures themselves (the statistical oracles)."""
from __future__ import annotations

import numpy as np
import pytest

from app.datasets import (
    density_sorting,
    discrete_runner,
    no_jump,
    sharp_jump,
    sparse_boundary,
)


@pytest.mark.unit
def test_sharp_jump_mean_has_exact_discontinuity_of_tau() -> None:
    d = sharp_jump(n=200_000, tau=3.0, noise_scale=0.0, heteroskedastic=False, seed=1)
    # very close to the cutoff, conditional means differ by exactly tau
    near = 0.02
    ml = d.y[(d.x < 0) & (d.x > -near)].mean()
    mr = d.y[(d.x >= 0) & (d.x < near)].mean()
    assert mr - ml == pytest.approx(3.0, abs=0.05)
    assert d.tau == 3.0


@pytest.mark.unit
def test_no_jump_is_continuous_at_cutoff() -> None:
    d = no_jump(n=200_000, noise_scale=0.0, heteroskedastic=False, seed=2)
    near = 0.02
    ml = d.y[(d.x < 0) & (d.x > -near)].mean()
    mr = d.y[(d.x >= 0) & (d.x < near)].mean()
    assert mr - ml == pytest.approx(0.0, abs=0.05)
    assert d.tau == 0.0


@pytest.mark.unit
def test_density_sorting_has_density_jump_but_continuous_mean() -> None:
    d = density_sorting(n=100_000, seed=3)
    # roughly right_density_multiplier/(1) ratio of masses
    n_r = np.sum(d.x >= 0)
    n_l = np.sum(d.x < 0)
    assert n_r / n_l == pytest.approx(2.5, abs=0.15)
    near = 0.03
    ml = d.y[(d.x < 0) & (d.x > -near)].mean()
    mr = d.y[(d.x >= 0) & (d.x < near)].mean()
    assert mr - ml == pytest.approx(0.0, abs=0.08)


@pytest.mark.unit
def test_sparse_boundary_has_support_hole() -> None:
    d = sparse_boundary(n=2000, hole=0.35, seed=4)
    assert np.min(np.abs(d.x)) >= 0.35 - 1e-9
    assert d.tau == 2.0  # jump exists but is not non-parametrically identified


@pytest.mark.unit
def test_discrete_runner_is_lattice_with_heap() -> None:
    d = discrete_runner(seed=5, step=0.1)
    uniq = np.unique(d.x)
    spacings = np.diff(np.unique(np.round(uniq, 6)))
    assert np.allclose(spacings, 0.1, atol=1e-6)
    assert np.sum(d.x == 0.0) >= 60


@pytest.mark.unit
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_fixtures_deterministic_given_seed(seed: int) -> None:
    a = sharp_jump(n=500, seed=seed)
    b = sharp_jump(n=500, seed=seed)
    np.testing.assert_array_equal(a.x, b.x)
    np.testing.assert_array_equal(a.y, b.y)
