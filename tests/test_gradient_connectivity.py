"""Connectivity table and gradient tests."""

from __future__ import annotations

import numpy as np
import pytest

from app.core.connectivity import iter_neighbors, neighbor_offsets
from app.core.gradient import gradient_magnitude


def test_connectivity_tables_are_fixed():
    assert neighbor_offsets(4) == ((-1, 0), (1, 0), (0, -1), (0, 1))
    assert len(neighbor_offsets(8)) == 8
    assert (-1, -1) in neighbor_offsets(8) and (1, 1) in neighbor_offsets(8)


def test_unsupported_connectivity_raises():
    with pytest.raises(ValueError, match="unsupported connectivity"):
        neighbor_offsets(6)


def test_iter_neighbors_clips_at_borders():
    corner = set(iter_neighbors(0, 0, 3, 3, 4))
    assert corner == {(1, 0), (0, 1)}
    center = set(iter_neighbors(1, 1, 3, 3, 8))
    assert len(center) == 8


def test_gradient_of_constant_image_is_zero():
    gradient = gradient_magnitude(np.full((5, 5), 7.0))
    np.testing.assert_array_equal(gradient, np.zeros((5, 5)))


def test_gradient_peaks_at_step_edge():
    image = np.zeros((6, 6))
    image[:, 3:] = 10.0
    gradient = gradient_magnitude(image)
    assert gradient.shape == image.shape
    assert np.all(gradient >= 0)
    peak_col = int(np.argmax(gradient.sum(axis=0)))
    assert peak_col in (2, 3), "gradient energy must concentrate at the step"


def test_smoothing_reduces_noise_gradient():
    rng = np.random.default_rng(7)
    image = rng.uniform(0, 1, size=(16, 16))
    rough = gradient_magnitude(image, sigma=0.0)
    smooth = gradient_magnitude(image, sigma=2.0)
    assert smooth.max() < rough.max()


def test_gradient_rejects_non_2d_and_negative_sigma():
    with pytest.raises(ValueError, match="2-D"):
        gradient_magnitude(np.zeros((2, 2, 2)))
    with pytest.raises(ValueError, match="sigma"):
        gradient_magnitude(np.zeros((2, 2)), sigma=-1.0)
