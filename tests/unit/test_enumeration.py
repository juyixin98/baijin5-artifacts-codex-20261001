"""Optimality by exhaustive enumeration.

For small images we enumerate all 2^n labelings and score each one with the
independent evaluator (:func:`graphcut.energy.evaluate_energy`). The solver
must match the enumerated optimum exactly — the reference never comes from
the graph/solver code path.
"""

from __future__ import annotations

import numpy as np
import pytest

from graphcut import AppConfig, run_pipeline
from graphcut.energy import evaluate_energy
from graphcut.models import SeedSet

from tests.conftest import enumerate_labelings, make_random_spec

CONFIG = AppConfig(scipy_cross_check=True)


@pytest.mark.parametrize("width,height", [(2, 2), (3, 2), (3, 3), (4, 2)])
def test_solver_matches_enumerated_optimum(rng, width, height):
    spec = make_random_spec(rng, width, height)
    result = run_pipeline(spec, CONFIG)

    best_energy, _ = enumerate_labelings(spec)
    assert result.energy.total == pytest.approx(best_energy, abs=1e-9)
    # the returned labeling's independently scored energy equals the optimum
    assert evaluate_energy(spec, result.labeling).total == pytest.approx(
        best_energy, abs=1e-9
    )
    # certificate: flow == cut, energy == constant + flow, scipy agrees
    assert result.certificate.consistent
    assert result.certificate.seeds_satisfied
    assert result.certificate.scipy_flow_value is not None


def test_multiple_random_instances(rng):
    for _ in range(5):
        spec = make_random_spec(rng, 3, 3)
        result = run_pipeline(spec, CONFIG)
        best_energy, _ = enumerate_labelings(spec)
        assert result.energy.total == pytest.approx(best_energy, abs=1e-9)


def test_seeded_optimum_matches_constrained_enumeration(rng):
    base = make_random_spec(rng, 3, 3)
    seeds = SeedSet(foreground=(4,), background=(0, 8))
    spec = type(base)(
        width=base.width, height=base.height,
        unary0=base.unary0, unary1=base.unary1,
        pairwise=base.pairwise, seeds=seeds,
    )
    result = run_pipeline(spec, CONFIG)

    flat = result.labeling.reshape(-1)
    assert flat[4] == 1 and flat[0] == 0 and flat[8] == 0

    forced = {4: 1, 0: 0, 8: 0}
    best_energy, _ = enumerate_labelings(spec, forced=forced)
    assert result.energy.total == pytest.approx(best_energy, abs=1e-9)
    assert result.certificate.seeds_satisfied


def test_seed_overrides_data_term(rng):
    # pixel strongly prefers background by data term, but is seeded foreground
    unary0 = np.zeros((1, 2))
    unary1 = np.array([[0.0, 100.0]])
    from graphcut.models import EnergySpec

    spec = EnergySpec(
        width=2, height=1, unary0=unary0, unary1=unary1,
        pairwise=(), seeds=SeedSet(foreground=(1,)),
    )
    result = run_pipeline(spec, CONFIG)
    assert result.labeling.reshape(-1)[1] == 1
    assert result.certificate.seeds_satisfied


def test_zero_smoothness_matches_argmin_energy(rng):
    base = make_random_spec(rng, 3, 3)
    spec = type(base)(
        width=base.width, height=base.height,
        unary0=base.unary0, unary1=base.unary1, pairwise=(),
    )
    result = run_pipeline(spec, CONFIG)
    expected = float(np.minimum(spec.unary0, spec.unary1).sum())
    assert result.energy.total == pytest.approx(expected, abs=1e-9)
    assert result.energy.smoothness == pytest.approx(0.0)
