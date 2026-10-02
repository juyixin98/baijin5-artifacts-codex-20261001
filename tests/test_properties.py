"""Algebraic properties of the reconstruction operator.

R must be: idempotent (R(R(x)) == R(x)), monotone in the marker
(m1 <= m2 => R(m1) <= R(m2)), bounded (marker <= R(marker) <= mask),
and extensive-only-within-the-mask.  Checked on seeded random inputs
for the queue engine (the reference engine shares the same fixpoint,
proven by the equivalence tests).
"""

from __future__ import annotations

import numpy as np
import pytest

from app.kernel import reconstruct_queue
from app.schemas import Connectivity


def _random_triplet(seed):
    rng = np.random.default_rng(seed)
    shape = tuple(rng.integers(6, 30, size=2))
    mask = rng.integers(0, 256, size=shape, dtype=np.uint8)
    m1 = np.minimum(mask, rng.integers(0, 256, size=shape, dtype=np.uint8))
    bump = rng.integers(0, 32, size=shape).astype(np.int32)
    m2 = np.minimum(mask, np.clip(m1.astype(np.int32) + bump, 0, 255).astype(np.uint8))
    # m1 <= m2 <= mask by construction
    return m1, m2, mask


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("connectivity", [Connectivity.FOUR, Connectivity.EIGHT])
def test_idempotent(seed, connectivity):
    _, marker, mask = _random_triplet(seed)
    once, _ = reconstruct_queue(marker, mask, connectivity)
    twice, stats = reconstruct_queue(once, mask, connectivity)
    np.testing.assert_array_equal(once, twice)
    # A fixpoint input must do zero updates.
    assert stats.changed_pixels == 0


@pytest.mark.parametrize("seed", range(6))
def test_monotone_in_marker(seed):
    m1, m2, mask = _random_triplet(seed)
    r1, _ = reconstruct_queue(m1, mask, Connectivity.EIGHT)
    r2, _ = reconstruct_queue(m2, mask, Connectivity.EIGHT)
    assert np.all(r1 <= r2)


@pytest.mark.parametrize("seed", range(6))
def test_bounded_between_marker_and_mask(seed):
    _, marker, mask = _random_triplet(seed)
    result, _ = reconstruct_queue(marker, mask, Connectivity.EIGHT)
    assert np.all(result >= marker)  # extensive
    assert np.all(result <= mask)  # never exceeds the mask


def test_monotone_in_mask():
    rng = np.random.default_rng(7)
    shape = (15, 15)
    mask1 = rng.integers(0, 256, size=shape, dtype=np.uint8)
    mask2 = np.clip(mask1.astype(np.int32) + 40, 0, 255).astype(np.uint8)
    marker = np.minimum(mask1, rng.integers(0, 256, size=shape, dtype=np.uint8))
    r1, _ = reconstruct_queue(marker, mask1, Connectivity.EIGHT)
    r2, _ = reconstruct_queue(marker, mask2, Connectivity.EIGHT)
    assert np.all(r1 <= r2)
