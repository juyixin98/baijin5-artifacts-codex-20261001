"""Seedless-region handling: active pixels unreachable from any seed are
labelled -1 and reported, never silently absorbed or dropped."""

from __future__ import annotations

import numpy as np

from watershed_backend.contracts import SeedPoint, build_markers_from_seeds
from watershed_backend.kernel import UNREACHED_LABEL, flood_watershed


def _split_mask() -> np.ndarray:
    # 4x6 grid, vertical masked wall at column 2: left half has the seed,
    # right half (columns 3-5) is an active component without any seed.
    mask = np.ones((4, 6), dtype=bool)
    mask[:, 2] = False
    return mask


def test_unreachable_component_labelled_minus_one_and_counted():
    gradient = np.zeros((4, 6), dtype=np.float64)
    markers = build_markers_from_seeds([SeedPoint(1, 0, 1)], gradient.shape)
    result = flood_watershed(gradient, markers, mask=_split_mask())

    assert (result.labels[:, 0:2] == 1).all(), "seeded component fully flooded"
    assert (result.labels[:, 2] == UNREACHED_LABEL).all(), "masked wall is -1"
    assert (result.labels[:, 3:] == UNREACHED_LABEL).all(), "seedless component is -1"
    # unreached counts *active* pixels only: the 12 pixels of the seedless
    # right half.  The 4 masked wall pixels are inactive, hence not counted.
    assert result.stats.unreached_pixels == 12
    assert result.stats.label_counts == {1: 8}


def test_masked_pixels_do_not_leak_labels():
    gradient = np.zeros((4, 6), dtype=np.float64)
    markers = build_markers_from_seeds([SeedPoint(1, 0, 1)], gradient.shape)
    result = flood_watershed(gradient, markers, mask=_split_mask())
    assert not (result.labels[:, 2:] > 0).any()
