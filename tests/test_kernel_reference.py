"""Kernel vs the INDEPENDENT brute-force reference.

The reference (app.reference) is a plain Python double loop over the
mathematical definition — it shares no code with the separable-envelope
kernel, so agreement here is evidence the envelope algorithm is right
rather than two implementations of the same mistake.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from app.kernel import edt
from app.reference import brute_force_edt

SPACINGS = [(1.0, 1.0), (0.5, 2.0), (3.0, 0.7), (1.5, 1.5)]
SHAPES = [(1, 1), (1, 9), (11, 1), (2, 3), (5, 5), (13, 4), (7, 11), (3, 15)]


def _assert_matches_reference(mask, sy, sx):
    """Every pixel: exact distance (rel 1e-9) AND exact source coordinate."""
    res = edt(mask, sy, sx)
    ref = brute_force_edt(mask, sy, sx)
    h, w = mask.shape
    for y in range(h):
        for x in range(w):
            rv = ref.distances[y][x]
            kv = float(res.distances[y, x])
            if math.isinf(rv):
                assert math.isinf(kv), (y, x, rv, kv)
                assert res.nearest_y[y, x] == -1
                assert res.nearest_x[y, x] == -1
                continue
            assert kv == pytest.approx(rv, rel=1e-9, abs=1e-10), (y, x, rv, kv)
            assert (int(res.nearest_y[y, x]), int(res.nearest_x[y, x])) == (
                ref.nearest_y[y][x], ref.nearest_x[y][x]
            ), (y, x, "source mismatch")


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("spacing", SPACINGS)
def test_handcrafted_masks_match_reference(shape, spacing):
    """Failure category: wrong distance or wrong nearest source coordinate."""
    sy, sx = spacing
    h, w = shape
    masks = []

    # Single source at the centre-ish pixel.
    m = np.zeros(shape, dtype=bool)
    m[h // 2, w // 2] = True
    masks.append(m)

    # Sources only at the four corners.
    m = np.zeros(shape, dtype=bool)
    m[0, 0] = m[0, w - 1] = m[h - 1, 0] = m[h - 1, w - 1] = True
    masks.append(m)

    # One full source row and one full source column.
    m = np.zeros(shape, dtype=bool)
    m[h // 2, :] = True
    m[:, w // 2] = True
    masks.append(m)

    # Checkerboard.
    yy, xx = np.indices(shape)
    masks.append(((yy + xx) % 2) == 0)

    for m in masks:
        _assert_matches_reference(m, sy, sx)


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 42])
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("spacing", SPACINGS)
def test_random_masks_match_reference(seed, shape, spacing):
    """Dense random masks force many exact-equidistance tie situations."""
    rng = np.random.default_rng(seed)
    sy, sx = spacing
    # Two densities: sparse (few ties) and dense (many ties).
    for p in (0.2, 0.7):
        mask = rng.random(shape) < p
        _assert_matches_reference(mask, sy, sx)


def test_no_sources_is_infinite_and_unlabelled():
    """Failure category: empty source set silently treated as zeros."""
    mask = np.zeros((4, 5), dtype=bool)
    res = edt(mask, 1.0, 1.0)
    assert res.has_sources is False
    assert np.isinf(res.distances).all()
    assert (res.nearest_y == -1).all()
    assert (res.nearest_x == -1).all()
    assert not res.ties.any()
    ref = brute_force_edt(mask)
    assert all(math.isinf(v) for row in ref.distances for v in row)


def test_all_sources_is_zero_self_labelled():
    mask = np.ones((3, 4), dtype=bool)
    res = edt(mask, 2.0, 0.5)
    assert res.has_sources is True
    assert np.all(res.distances == 0.0)
    yy, xx = np.indices((3, 4))
    assert np.array_equal(res.nearest_y, yy)
    assert np.array_equal(res.nearest_x, xx)
    assert not res.ties.any()


def test_single_source_concrete_values_isotropic():
    """Failure category: Manhattan distance masquerading as Euclidean."""
    mask = np.zeros((3, 3), dtype=bool)
    mask[1, 1] = True
    res = edt(mask, 1.0, 1.0)
    expected = np.array([
        [math.sqrt(2), 1.0, math.sqrt(2)],
        [1.0, 0.0, 1.0],
        [math.sqrt(2), 1.0, math.sqrt(2)],
    ])
    assert np.allclose(res.distances, expected)
    # The diagonal neighbour must be sqrt(2), NOT the Manhattan value 2.
    assert res.distances[2, 2] == pytest.approx(math.sqrt(2))
    assert not math.isclose(res.distances[2, 2], 2.0, rel_tol=1e-6)
    # Every pixel names the sole source.
    assert (res.nearest_y == 1).all()
    assert (res.nearest_x == 1).all()


def test_anisotropic_spacing_concrete_values():
    """sy=2, sx=0.5: physical distance from (0,0) is sqrt((2y)^2+(0.5x)^2)."""
    mask = np.zeros((3, 5), dtype=bool)
    mask[0, 0] = True
    res = edt(mask, 2.0, 0.5)
    for y in range(3):
        for x in range(5):
            assert res.distances[y, x] == pytest.approx(
                math.hypot(2.0 * y, 0.5 * x)
            )
            assert int(res.nearest_y[y, x]) == 0
            assert int(res.nearest_x[y, x]) == 0
    # A deliberately non-symmetric pair: (1,4) -> sqrt(4 + 4) = sqrt(8).
    assert res.distances[1, 4] == pytest.approx(math.sqrt(8))


def test_long_thin_image_1d_tie_picks_lex_smallest():
    mask = np.zeros((1, 5), dtype=bool)
    mask[0, 0] = True
    mask[0, 4] = True
    res = edt(mask)
    assert np.allclose(res.distances[0], [0, 1, 2, 1, 0])
    # Pixel 2 is exactly equidistant: lexicographically smallest source wins.
    assert int(res.nearest_y[0, 2]) == 0
    assert int(res.nearest_x[0, 2]) == 0
    assert bool(res.ties[0, 2]) is True
    assert bool(res.ties[0, 1]) is False


def test_vertical_thin_image_tie():
    mask = np.zeros((5, 1), dtype=bool)
    mask[0, 0] = True
    mask[4, 0] = True
    res = edt(mask, spacing_y=0.3, spacing_x=1.0)
    assert res.distances[:, 0] == pytest.approx(
        [0, 0.3, 0.6, 0.3, 0.0]
    )
    assert int(res.nearest_y[2, 0]) == 0  # lex-smallest on the tie
    assert bool(res.ties[2, 0]) is True


def test_2d_tie_across_diagonal_sources_picks_lex_smallest():
    """Tie that only becomes visible in pass 2 (sources in different rows)."""
    mask = np.zeros((3, 3), dtype=bool)
    mask[0, 2] = True  # label 2
    mask[2, 0] = True  # label 6 after pass-1 encoding
    res = edt(mask)
    # (1,1) is sqrt(2) from both sources.
    assert res.distances[1, 1] == pytest.approx(math.sqrt(2))
    assert (int(res.nearest_y[1, 1]), int(res.nearest_x[1, 1])) == (0, 2)
    assert bool(res.ties[1, 1]) is True
    # (0,0) is closer to (0,2)? distance 2 vs sqrt(8) -> source (0,2).
    assert (int(res.nearest_y[0, 0]), int(res.nearest_x[0, 0])) == (0, 2)
    assert res.distances[0, 0] == pytest.approx(2.0)


def test_validation_categories():
    with pytest.raises(ValueError, match="2-D"):
        edt(np.zeros((3,), dtype=bool))
    with pytest.raises(ValueError, match="non-empty"):
        edt(np.zeros((0, 4), dtype=bool))
    with pytest.raises(ValueError, match="strictly positive"):
        edt(np.ones((2, 2), dtype=bool), spacing_y=0.0, spacing_x=1.0)
    with pytest.raises(ValueError, match="finite"):
        edt(np.ones((2, 2), dtype=bool), spacing_y=float("nan"),
            spacing_x=1.0)
