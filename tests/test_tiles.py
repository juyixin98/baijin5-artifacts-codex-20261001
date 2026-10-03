"""Tiled (chunked) job tests: equivalence with whole-image kernels and
sweep-count recording."""
import numpy as np
import pytest

from fixtures import ALL_FIXTURES
from geodesic_recon.kernel import reconstruct_queue, reconstruct_sync
from geodesic_recon.tiles import TiledTrace, reconstruct_tiled, tile_slices


@pytest.mark.parametrize("factory", ALL_FIXTURES, ids=lambda f: f().name)
@pytest.mark.parametrize("tile_shape", [(1, 1), (2, 3), (3, 2), (100, 100)])
def test_tiled_matches_sync_and_queue(factory, tile_shape):
    fx = factory()
    expected, _ = reconstruct_sync(fx.marker, fx.mask, 4)
    result, trace = reconstruct_tiled(fx.marker, fx.mask, tile_shape=tile_shape, connectivity=4)
    np.testing.assert_array_equal(result, expected)
    assert isinstance(trace, TiledTrace)
    assert trace.sweeps == len(trace.changed_per_sweep)
    assert trace.changed_per_sweep[-1] == 0  # terminating sweep is stable


@pytest.mark.parametrize("connectivity", [4, 8])
def test_tiled_random_equivalence(connectivity):
    rng = np.random.default_rng(42)
    mask = rng.uniform(0, 100, size=(17, 23))
    marker = mask * rng.uniform(0, 1, size=(17, 23))
    expected, _ = reconstruct_queue(marker, mask, connectivity)
    for tile_shape in [(4, 4), (5, 7), (1, 23), (17, 1)]:
        result, trace = reconstruct_tiled(
            marker, mask, tile_shape=tile_shape, connectivity=connectivity
        )
        np.testing.assert_array_equal(result, expected)
        assert trace.sweeps >= 2  # at least one working sweep + stable sweep


def test_tile_slices_cover_exactly_once():
    slices = list(tile_slices((7, 5), (3, 2)))
    covered = np.zeros((7, 5), dtype=int)
    for ys, xs in slices:
        covered[ys, xs] += 1
    assert (covered == 1).all()


def test_tiled_rejects_bad_tile_shape():
    with pytest.raises(ValueError):
        reconstruct_tiled(np.zeros((2, 2)), np.ones((2, 2)), tile_shape=(0, 2))
