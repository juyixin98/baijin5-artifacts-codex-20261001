"""Shared fixtures: synthetic aligned-sequence builders.

All data is synthetic and deterministic - no external services, files,
or real biological data involved.
"""

from __future__ import annotations

import pytest


def make_pair(length: int, n_transition: int, n_transversion: int,
              a_base: str = "A", ti_base: str = "G", tv_base: str = "C"):
    """Build two aligned sequences with exact mismatch composition.

    seq_a is `a_base` everywhere; seq_b introduces `n_transition`
    transitions (a_base -> ti_base) and `n_transversion` transversions
    (a_base -> tv_base). Requires a_base/ti_base to be a transition pair
    and a_base/tv_base a transversion pair (default A->G / A->C).
    """
    assert n_transition + n_transversion <= length
    seq_a = a_base * length
    seq_b = (
        ti_base * n_transition
        + tv_base * n_transversion
        + a_base * (length - n_transition - n_transversion)
    )
    return seq_a, seq_b


@pytest.fixture()
def pair_factory():
    return make_pair
