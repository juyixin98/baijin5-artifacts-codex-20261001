"""Kernel tests: hand-computed cases plus randomized cross-checks against
two independent oracles — the in-repo brute force (distances AND labels)
and scipy.ndimage.distance_transform_edt (distances only)."""

import math

import numpy as np
import pytest
from scipy.ndimage import distance_transform_edt

from edt_service.kernel import INF, NO_SOURCE, dt1d, edt2d
from edt_service.reference import brute_force_edt


# ---------------------------------------------------------------- 1-D ---

def test_dt1d_single_source():
    d, idx = dt1d(np.array([0.0, INF, INF]), 1.0)
    assert d.tolist() == [0.0, 1.0, 4.0]
    assert idx.tolist() == [0, 0, 0]


def test_dt1d_anisotropic_spacing():
    d, idx = dt1d(np.array([0.0, INF, INF]), 2.5)
    assert d.tolist() == [0.0, 6.25, 25.0]


def test_dt1d_all_inf():
    d, idx = dt1d(np.array([INF, INF]), 1.0)
    assert np.isinf(d).all()
    assert (idx == NO_SOURCE).all()


def test_dt1d_tie_prefers_lower_index():
    # Two identical parabolas at 0 and 2: position 1 is equidistant.
    d, idx = dt1d(np.array([0.0, INF, 0.0]), 1.0)
    assert d[1] == 1.0
    assert idx[1] == 0


# ------------------------------------------------------- hand-computed ---

def test_single_row_distances_and_labels():
    mask = np.array([[1, 0, 0]], dtype=bool)
    dist, labels = edt2d(mask)
    assert dist.tolist() == [[0.0, 1.0, 2.0]]
    assert labels.tolist() == [[0, 0, 0]]


def test_diagonal_distance_is_euclidean_not_manhattan():
    # A Manhattan/chamfer kernel would report 2.0 at (1,1); exact is sqrt(2).
    mask = np.array([[1, 0], [0, 0]], dtype=bool)
    dist, labels = edt2d(mask)
    assert dist[1, 1] == pytest.approx(math.sqrt(2.0))
    assert dist[0, 1] == 1.0 and dist[1, 0] == 1.0
    assert (labels == 0).all()


def test_anisotropic_spacing_row_axis():
    mask = np.array([[1], [0], [0]], dtype=bool)
    dist, _ = edt2d(mask, spacing=(3.0, 1.0))
    assert dist[:, 0].tolist() == [0.0, 3.0, 6.0]


def test_anisotropic_spacing_2d():
    mask = np.array([[1, 0], [0, 0]], dtype=bool)
    dist, _ = edt2d(mask, spacing=(3.0, 1.0))
    assert dist[1, 1] == pytest.approx(math.hypot(3.0, 1.0))
    assert dist[1, 0] == 3.0
    assert dist[0, 1] == 1.0


def test_tie_break_smaller_column_wins():
    mask = np.array([[1, 0, 1]], dtype=bool)
    dist, labels = edt2d(mask)
    assert dist[0, 1] == 1.0
    assert labels[0, 1] == 0  # source at col 0, not col 2


def test_tie_break_smaller_row_wins_within_column():
    mask = np.array([[1], [0], [1]], dtype=bool)
    dist, labels = edt2d(mask)
    assert dist[1, 0] == 1.0
    assert labels[1, 0] == 0  # source at row 0, not row 2


def test_tie_break_column_dominates_row():
    # Sources (0,2) and (2,0); pixel (1,1) is equidistant (sqrt2) to both.
    # Contract: (distance, column, row) -> column 0 wins -> source (2,0).
    mask = np.array([[0, 0, 1], [0, 0, 0], [1, 0, 0]], dtype=bool)
    dist, labels = edt2d(mask)
    assert dist[1, 1] == pytest.approx(math.sqrt(2.0))
    assert labels[1, 1] == 2 * 3 + 0


def test_empty_raster_semantics():
    dist, labels = edt2d(np.zeros((3, 4), dtype=bool))
    assert np.isinf(dist).all()
    assert (labels == NO_SOURCE).all()


def test_full_raster_semantics():
    mask = np.ones((3, 4), dtype=bool)
    dist, labels = edt2d(mask)
    assert (dist == 0.0).all()
    assert np.array_equal(labels, np.arange(12).reshape(3, 4))


# --------------------------------------------- randomized vs oracles ---

@pytest.mark.parametrize("seed", range(40))
def test_matches_brute_force_random(seed):
    rng = np.random.default_rng(seed)
    h = int(rng.integers(1, 15))
    w = int(rng.integers(1, 15))
    mask = rng.random((h, w)) < rng.choice([0.05, 0.3, 0.7])
    spacing = [(1.0, 1.0), (2.0, 1.0), (1.0, 3.0), (0.5, 2.0)][seed % 4]
    d_kernel, l_kernel = edt2d(mask, spacing)
    d_ref, l_ref = brute_force_edt(mask, spacing)
    assert np.array_equal(np.isinf(d_kernel), np.isinf(d_ref))
    assert np.allclose(d_kernel, d_ref, rtol=1e-12, atol=1e-12)
    assert np.array_equal(l_kernel, l_ref)


@pytest.mark.parametrize("shape", [(1, 300), (300, 1), (2, 500), (500, 2), (3, 97)])
def test_long_thin_matches_brute_force(shape):
    rng = np.random.default_rng(sum(shape))
    mask = rng.random(shape) < 0.02
    mask[0, 0] = True  # guarantee at least one source
    d_kernel, l_kernel = edt2d(mask, (1.0, 1.0))
    d_ref, l_ref = brute_force_edt(mask, (1.0, 1.0))
    assert np.allclose(d_kernel, d_ref, rtol=1e-12, atol=1e-12)
    assert np.array_equal(l_kernel, l_ref)


@pytest.mark.parametrize("seed", range(10))
def test_distances_match_scipy(seed):
    rng = np.random.default_rng(1000 + seed)
    h = int(rng.integers(1, 25))
    w = int(rng.integers(1, 25))
    mask = rng.random((h, w)) < 0.25
    mask[0, 0] = True  # scipy is undefined for source-less rasters
    spacing = [(1.0, 1.0), (2.0, 0.5), (0.25, 4.0)][seed % 3]
    d_kernel, _ = edt2d(mask, spacing)
    d_scipy = distance_transform_edt(~mask, sampling=spacing)
    assert np.allclose(d_kernel, d_scipy, rtol=1e-10, atol=1e-10)


def test_tie_heavy_checkerboard_matches_brute_force():
    # Maximally tie-prone: sources on every other pixel.
    mask = np.indices((8, 8)).sum(axis=0) % 2 == 0
    d_kernel, l_kernel = edt2d(mask, (1.0, 1.0))
    d_ref, l_ref = brute_force_edt(mask, (1.0, 1.0))
    assert np.allclose(d_kernel, d_ref)
    assert np.array_equal(l_kernel, l_ref)
