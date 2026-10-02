"""Equivalence of the queue engine and the synchronous reference.

For every fixture and a battery of seeded random images, the FIFO-queue
schedule must produce exactly the fixpoint of synchronous iteration —
this is the property Vincent's algorithm is built on, and we assert it
directly rather than comparing pictures.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.kernel import reconstruct_queue, reconstruct_reference
from app.schemas import Connectivity
from app.tiling import reconstruct_tiled
from tests.fixtures import ALL_FIXTURES

RNG = np.random.default_rng(20261002)


def _random_pair(shape, levels=256):
    mask = RNG.integers(0, levels, size=shape, dtype=np.uint8)
    # marker <= mask by construction
    marker = np.minimum(
        mask, RNG.integers(0, levels, size=shape, dtype=np.uint8)
    )
    return marker, mask


@pytest.mark.parametrize("fixture_name", sorted(ALL_FIXTURES))
@pytest.mark.parametrize("connectivity", [Connectivity.FOUR, Connectivity.EIGHT])
def test_queue_equals_reference_on_fixtures(fixture_name, connectivity):
    marker, mask, _ = ALL_FIXTURES[fixture_name]()
    expected, ref_stats = reconstruct_reference(marker, mask, connectivity)
    result, queue_stats = reconstruct_queue(marker, mask, connectivity)
    np.testing.assert_array_equal(result, expected)
    # The queue engine must do strictly less "work" than full-image
    # sweeps on sparse fixtures: pops are bounded by pixels * sweeps.
    assert queue_stats.queue_pops <= marker.size * ref_stats.iterations


@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("connectivity", [Connectivity.FOUR, Connectivity.EIGHT])
def test_queue_equals_reference_on_random(seed, connectivity, convergence_log):
    rng = np.random.default_rng(seed)
    shape = tuple(int(d) for d in rng.integers(5, 40, size=2))
    mask = rng.integers(0, 256, size=shape, dtype=np.uint8)
    marker = np.minimum(mask, rng.integers(0, 256, size=shape, dtype=np.uint8))

    expected, ref_stats = reconstruct_reference(marker, mask, connectivity)
    result, queue_stats = reconstruct_queue(marker, mask, connectivity)
    convergence_log.record(
        test="queue_vs_reference_random",
        seed=seed,
        shape=list(shape),
        connectivity=int(connectivity),
        reference_iterations=ref_stats.iterations,
        queue_pops=queue_stats.queue_pops,
    )
    np.testing.assert_array_equal(result, expected)


@pytest.mark.parametrize("tile_size", [8, 16, 64])
@pytest.mark.parametrize("seed", range(4))
def test_tiled_equals_reference(tile_size, seed, convergence_log):
    rng = np.random.default_rng(1000 + seed)
    shape = (37, 53)  # deliberately not a multiple of any tile size
    mask = rng.integers(0, 256, size=shape, dtype=np.uint8)
    marker = np.minimum(mask, rng.integers(0, 256, size=shape, dtype=np.uint8))

    expected, _ = reconstruct_reference(marker, mask, Connectivity.EIGHT)
    result, stats = reconstruct_tiled(
        marker, mask, Connectivity.EIGHT, tile_size=tile_size
    )
    convergence_log.record(
        test="tiled_vs_reference",
        tile_size=tile_size,
        seed=seed,
        sweeps=stats.iterations,
    )
    np.testing.assert_array_equal(result, expected)


@pytest.mark.parametrize("fixture_name", sorted(ALL_FIXTURES))
def test_tiled_equals_reference_on_fixtures(fixture_name):
    marker, mask, _ = ALL_FIXTURES[fixture_name]()
    expected, _ = reconstruct_reference(marker, mask, Connectivity.EIGHT)
    result, _ = reconstruct_tiled(
        marker, mask, Connectivity.EIGHT, tile_size=8
    )
    np.testing.assert_array_equal(result, expected)
