"""Shared fixtures and reference helpers for the test-suite.

``enumerate_labelings`` is the independent reference: it scores every
labeling directly through :func:`graphcut.energy.evaluate_energy`, which
shares no code with the graph construction or the solver. Optimal energies
in the tests therefore never come from the implementation under test.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from graphcut.energy import evaluate_energy
from graphcut.models import EnergySpec, PairwiseTerm, SeedSet
from graphcut.specs import neighbor_pairs


def make_random_spec(
    rng: np.random.Generator,
    width: int,
    height: int,
    *,
    max_unary: float = 5.0,
    max_pairwise: float = 3.0,
) -> EnergySpec:
    """Random non-negative, submodular spec on the 4-neighborhood."""
    unary0 = rng.uniform(0.0, max_unary, (height, width))
    unary1 = rng.uniform(0.0, max_unary, (height, width))
    terms = []
    for p, q in neighbor_pairs(width, height):
        v00 = rng.uniform(0.0, max_pairwise)
        v11 = rng.uniform(0.0, max_pairwise)
        # enforce submodularity by construction: v01+v10 >= v00+v11
        extra = rng.uniform(0.0, max_pairwise)
        split = rng.uniform(0.0, 1.0)
        v01 = v00 / 2 + extra * split
        v10 = v11 / 2 + extra * (1.0 - split) + (v00 + v11) / 2
        # v01 + v10 = v00 + v11 + extra >= v00 + v11
        terms.append(PairwiseTerm(p, q, v00, v01, v10, v11))
    return EnergySpec(
        width=width, height=height,
        unary0=unary0, unary1=unary1,
        pairwise=tuple(terms),
    )


def enumerate_labelings(
    spec: EnergySpec,
    *,
    forced: dict[int, int] | None = None,
) -> tuple[float, np.ndarray]:
    """Brute-force optimum: returns (min_energy, best_labeling).

    ``forced`` maps pixel index -> required label (used for seeded runs).
    """
    forced = forced or {}
    n = spec.num_pixels
    best_energy = np.inf
    best_labeling: np.ndarray | None = None
    free = [i for i in range(n) if i not in forced]
    for bits in itertools.product((0, 1), repeat=len(free)):
        labeling = np.zeros(n, dtype=np.int8)
        for idx, value in forced.items():
            labeling[idx] = value
        for idx, value in zip(free, bits):
            labeling[idx] = value
        energy = evaluate_energy(
            spec, labeling.reshape(spec.height, spec.width)
        ).total
        if energy < best_energy - 1e-12:
            best_energy = energy
            best_labeling = labeling
    assert best_labeling is not None
    return best_energy, best_labeling.reshape(spec.height, spec.width)


@pytest.fixture()
def rng() -> np.random.Generator:
    return np.random.default_rng(20261003)
