"""Flat plateau fixture: equal-elevation processing order is deterministic,
the ridge line is fixed, and 4- vs 8-connectivity behave as specified."""

from __future__ import annotations

import numpy as np

from tests.fixtures.synthetic import flat_plateau
from tests.helpers import run_from_seeds


def test_plateau_matches_hand_computed_reference():
    gradient, seeds, expected_labels, expected_boundary = flat_plateau()
    result = run_from_seeds(gradient, seeds)
    np.testing.assert_array_equal(result.labels, expected_labels)
    np.testing.assert_array_equal(result.boundary, expected_boundary)


def test_plateau_ridge_is_straight_vertical_line_in_column_3():
    gradient, seeds, _, _ = flat_plateau()
    result = run_from_seeds(gradient, seeds)
    np.testing.assert_array_equal(result.labels[:, 3], np.zeros(3, dtype=np.int32))
    assert set(np.unique(result.labels[:, :3])) == {1}
    assert set(np.unique(result.labels[:, 4:])) == {2}


def test_plateau_order_does_not_depend_on_seed_order():
    gradient, seeds, expected_labels, _ = flat_plateau()
    shuffled = run_from_seeds(gradient, list(reversed(seeds)))
    np.testing.assert_array_equal(shuffled.labels, expected_labels)


def test_plateau_with_4_connectivity_gives_same_fixed_ridge():
    gradient, seeds, expected_labels, _ = flat_plateau()
    result = run_from_seeds(gradient, seeds, connectivity=4)
    np.testing.assert_array_equal(result.labels, expected_labels)
