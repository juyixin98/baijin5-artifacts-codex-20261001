"""Saddle fixture: ridge forms on saddle pixels; unseeded local minima are
absorbed deterministically by the first flood front that reaches them."""

from __future__ import annotations

import numpy as np

from tests.fixtures.synthetic import saddle
from tests.helpers import assert_boundary_separates, run_from_seeds


def test_saddle_matches_hand_computed_reference():
    gradient, seeds, expected_labels, expected_boundary = saddle()
    result = run_from_seeds(gradient, seeds)
    np.testing.assert_array_equal(result.labels, expected_labels)
    np.testing.assert_array_equal(result.boundary, expected_boundary)


def test_unseeded_minima_are_absorbed_not_left_unlabelled():
    gradient, seeds, _, _ = saddle()
    result = run_from_seeds(gradient, seeds)
    # (0,2) and (2,0) are elevation-1 minima with no seed: the flood from
    # seed 1 reaches them first, so they deterministically join basin 1.
    assert result.labels[0, 2] == 1
    assert result.labels[2, 0] == 1
    assert result.stats.unreached_pixels == 0


def test_saddle_ridge_pixels_are_where_fronts_meet():
    gradient, seeds, _, _ = saddle()
    result = run_from_seeds(gradient, seeds)
    ridge = {(1, 1), (1, 2), (2, 1)}
    found = set(zip(*np.nonzero(result.boundary)))
    found = {(int(r), int(c)) for r, c in found}
    assert found == ridge


def test_saddle_boundary_separates_basins():
    gradient, seeds, _, _ = saddle()
    result = run_from_seeds(gradient, seeds)
    assert_boundary_separates(result.labels)
