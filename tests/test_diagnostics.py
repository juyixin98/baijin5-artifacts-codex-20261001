"""Tests for discreteness / heaping / density / support diagnostics."""
from __future__ import annotations

import numpy as np
import pytest

from app.datasets import density_sorting, discrete_runner, no_jump, sparse_boundary
from app.diagnostics import (
    density_diagnostic,
    discreteness_diagnostics,
    is_lattice,
    mccrary_density_test,
)


@pytest.mark.unit
def test_lattice_detection_on_grid_and_continuous() -> None:
    grid = np.arange(-1.0, 1.0, 0.1)
    assert is_lattice(grid)[0] is True
    rng = np.random.default_rng(0)
    cont = rng.uniform(-1, 1, 1000)
    assert is_lattice(cont)[0] is False


@pytest.mark.unit
def test_discrete_and_heap_diagnostics_flag_lattice_and_cutoff_mass() -> None:
    d = discrete_runner(seed=1)
    diags = {p["code"].value: p for p in discreteness_diagnostics(d.x, 0.0)}
    assert "discrete_running_var" in diags
    assert diags["discrete_running_var"]["details"]["lattice"] is True
    assert "mass_at_cutoff" in diags
    assert diags["mass_at_cutoff"]["details"]["n_at_cutoff"] >= 60


@pytest.mark.unit
def test_continuous_runner_has_no_discreteness_diagnostic() -> None:
    d = no_jump(seed=2)
    diags = discreteness_diagnostics(d.x, 0.0)
    assert diags == []


@pytest.mark.unit
def test_mccrary_flags_sort_and_stays_silent_on_continuous_density() -> None:
    sort = density_sorting(n=8000, seed=3)
    res = mccrary_density_test(sort.x, 0.0)
    assert res.interpretable is True
    # true log density ratio = log(2.5) ~= 0.916
    assert res.theta == pytest.approx(np.log(2.5), abs=0.3)
    assert res.p_value < 0.01

    clean = no_jump(seed=4)
    res_clean = mccrary_density_test(clean.x, 0.0)
    assert res_clean.interpretable is True
    assert abs(res_clean.theta) < 0.35
    assert res_clean.p_value > 0.05


@pytest.mark.unit
def test_density_diagnostic_severity_and_lattice_caveat() -> None:
    sort = density_sorting(seed=5)
    payload = density_diagnostic(sort.x, 0.0)
    assert payload["severity"].value == "warning"
    assert payload["details"]["interpretable"] is True

    grid = discrete_runner(seed=6)
    payload_grid = density_diagnostic(grid.x, 0.0)
    # lattice caveat recorded regardless of significance
    assert payload_grid["details"]["lattice_runner"] is True


@pytest.mark.unit
def test_mccrary_uninterpretable_with_tiny_samples() -> None:
    x = np.array([-0.9, -0.8, 0.8, 0.9])
    res = mccrary_density_test(x, 0.0)
    assert res.interpretable is False
    assert np.isnan(res.theta)
