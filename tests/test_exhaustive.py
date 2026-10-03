"""Exhaustive-reference verification for short sequences.

For every short random pair, the DP core's cost must equal the minimum
over *all* legal paths enumerated independently by ``tests/reference.py``,
and the returned path must itself be legal: monotone under the step
pattern, inside the window, endpoints correct, and its re-summed cell cost
equal to the reported cost.
"""

import math

import numpy as np
import pytest

from dtw_service.banded import banded_dtw
from dtw_service.constraints import DEFAULT_STEP_PATTERN, SakoeChibaWindow
from dtw_service.dense import dense_dtw

from tests.reference import reference_min_cost

RNG = np.random.default_rng(20261003)


def _random_pair(n: int, m: int) -> tuple[np.ndarray, np.ndarray]:
    return RNG.integers(0, 5, size=n).astype(float), RNG.integers(0, 5, size=m).astype(float)


CASES = [
    (n, m, r)
    for n in range(1, 6)
    for m in range(1, 6)
    for r in range(0, 4)
]


@pytest.mark.parametrize("n,m,radius", CASES)
def test_dense_matches_exhaustive_reference(n, m, radius):
    a, b = _random_pair(n, m)
    window = SakoeChibaWindow(radius)
    res = dense_dtw(a, b, window)
    expected = reference_min_cost(a.tolist(), b.tolist(), radius)

    if math.isinf(expected):
        assert math.isinf(res.cost), "DP found a path the exhaustive search missed"
        assert res.path is None
        return

    assert res.cost == pytest.approx(expected), (
        f"DP cost {res.cost} != exhaustive minimum {expected}"
    )
    _assert_legal_path(res.path, a, b, radius)
    recomputed = sum(abs(a[i] - b[j]) for i, j in res.path)
    assert recomputed == pytest.approx(res.cost)


@pytest.mark.parametrize("n,m,radius", CASES)
def test_banded_matches_exhaustive_reference(n, m, radius):
    a, b = _random_pair(n, m)
    window = SakoeChibaWindow(radius)
    res = banded_dtw(a, b, window)
    expected = reference_min_cost(a.tolist(), b.tolist(), radius)
    if math.isinf(expected):
        assert math.isinf(res.cost) and res.path is None
        return
    assert res.cost == pytest.approx(expected)
    _assert_legal_path(res.path, a, b, radius)


def _assert_legal_path(path, a, b, radius):
    assert path[0] == (0, 0)
    assert path[-1] == (len(a) - 1, len(b) - 1)
    legal_steps = set(DEFAULT_STEP_PATTERN.steps)
    for (i0, j0), (i1, j1) in zip(path, path[1:]):
        assert (i1 - i0, j1 - j0) in legal_steps, "path violates the step pattern"
    for i, j in path:
        assert abs(i - j) <= radius, "path leaves the Sakoe-Chiba window"
