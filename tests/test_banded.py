"""Banded storage: agreement with the dense core and memory shape."""

import numpy as np
import pytest

from dtw_service.banded import banded_dtw
from dtw_service.constraints import SakoeChibaWindow
from dtw_service.dense import dense_dtw

RNG = np.random.default_rng(7)


@pytest.mark.parametrize("n,m,radius", [
    (40, 40, 4),
    (60, 55, 6),
    (50, 50, 0),
    (80, 70, 10),
    (30, 45, 15),
])
def test_banded_matches_dense_on_random_sequences(n, m, radius):
    a = RNG.normal(size=n)
    b = RNG.normal(size=m)
    window = SakoeChibaWindow(radius)
    dense = dense_dtw(a, b, window)
    banded = banded_dtw(a, b, window)
    assert banded.cost == pytest.approx(dense.cost)
    assert banded.path == dense.path


def test_banded_allocates_only_the_band():
    n, m, radius = 200, 200, 5
    a = RNG.normal(size=n)
    b = RNG.normal(size=m)
    res = banded_dtw(a, b, SakoeChibaWindow(radius))
    assert res.band_shape == (n, 2 * radius + 1)
    # Dense would allocate n * m = 40000 cells; the band holds n * 11.
    assert res.band_shape[0] * res.band_shape[1] < n * m // 10


def test_banded_unreachable_endpoint_is_explicit():
    # |n - m| = 4 > radius 1: no legal path can exist.
    a = np.zeros(10)
    b = np.zeros(6)
    res = banded_dtw(a, b, SakoeChibaWindow(radius=1))
    assert res.cost == float("inf")
    assert res.path is None
