"""Tiled execution must be bit-identical to the full-image kernel."""

from __future__ import annotations

import numpy as np
import pytest

from app.kernel import thin
from app.tiling import partition, thin_tiled
from app.samples import cross_tile_stroke, fork, ring, thin_bridge
from app.validation import count_components


def test_partition_covers_image_without_gaps() -> None:
    tiles = partition((24, 24), tile_size=8)
    assert len(tiles) == 9
    covered = np.zeros((24, 24), dtype=int)
    for t in tiles:
        covered[t.row0 : t.row1, t.col0 : t.col1] += 1
    assert (covered == 1).all()


def test_partition_handles_ragged_edges() -> None:
    tiles = partition((25, 26), tile_size=8)
    assert len(tiles) == 16
    covered = np.zeros((25, 26), dtype=int)
    for t in tiles:
        covered[t.row0 : t.row1, t.col0 : t.col1] += 1
    assert (covered == 1).all()


def test_partition_rejects_bad_tile_size() -> None:
    with pytest.raises(ValueError):
        partition((8, 8), tile_size=0)


@pytest.mark.parametrize("builder", [ring, thin_bridge, fork, cross_tile_stroke])
def test_tiled_matches_full_image_reference(builder) -> None:
    image = builder()
    reference = thin(image)
    tiled = thin_tiled(image, tile_size=8)
    assert tiled.converged and reference.converged
    np.testing.assert_array_equal(tiled.skeleton, reference.skeleton)
    assert tiled.deletions_per_round == reference.deletions_per_round
    assert tiled.rounds == reference.rounds


def test_tiled_matches_full_on_seeded_noise() -> None:
    rng = np.random.default_rng(20261003)
    image = (rng.random((33, 35)) > 0.5).astype(np.uint8)
    reference = thin(image)
    tiled = thin_tiled(image, tile_size=7)
    np.testing.assert_array_equal(tiled.skeleton, reference.skeleton)
    assert tiled.deletions_per_round == reference.deletions_per_round


def test_cross_tile_stroke_stays_connected_across_boundaries() -> None:
    image = cross_tile_stroke(size=24, thickness=3)
    tiled = thin_tiled(image, tile_size=8)
    assert tiled.tile_count == 9
    assert count_components(tiled.skeleton) == 1


def test_convergence_requires_globally_clean_round() -> None:
    tiled = thin_tiled(ring(), tile_size=8)
    assert tiled.converged
    assert tiled.deletions_per_round[-1] == (0, 0)
    assert tiled.rounds > 1  # real work happened before the clean round
