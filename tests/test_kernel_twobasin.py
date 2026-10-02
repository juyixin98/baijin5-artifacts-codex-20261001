"""Two-basin fixture: exact hand-computed reference, seed preservation,
permutation stability of the seed list."""

from __future__ import annotations

import numpy as np

from tests.fixtures.synthetic import two_basin
from tests.helpers import assert_boundary_separates, run_from_seeds


def test_matches_hand_computed_labels_and_boundary():
    gradient, seeds, expected_labels, expected_boundary = two_basin()
    result = run_from_seeds(gradient, seeds)
    np.testing.assert_array_equal(result.labels, expected_labels)
    np.testing.assert_array_equal(result.boundary, expected_boundary)


def test_seeds_are_preserved():
    gradient, seeds, _, _ = two_basin()
    result = run_from_seeds(gradient, seeds)
    for seed in seeds:
        assert result.labels[seed.row, seed.col] == seed.label


def test_seed_list_permutation_is_stable():
    gradient, seeds, expected_labels, _ = two_basin()
    forward = run_from_seeds(gradient, seeds)
    reversed_result = run_from_seeds(gradient, list(reversed(seeds)))
    np.testing.assert_array_equal(forward.labels, expected_labels)
    np.testing.assert_array_equal(reversed_result.labels, expected_labels)


def test_repeated_runs_are_identical():
    gradient, seeds, expected_labels, _ = two_basin()
    first = run_from_seeds(gradient, seeds)
    second = run_from_seeds(gradient, seeds)
    np.testing.assert_array_equal(first.labels, second.labels)
    np.testing.assert_array_equal(first.boundary, second.boundary)


def test_boundary_separates_basins():
    gradient, seeds, _, _ = two_basin()
    result = run_from_seeds(gradient, seeds)
    assert_boundary_separates(result.labels)


def test_stats_account_for_every_pixel():
    gradient, seeds, _, _ = two_basin()
    result = run_from_seeds(gradient, seeds)
    stats = result.stats
    assert stats.seed_pixels == 2
    assert stats.boundary_pixels == 5
    assert stats.unreached_pixels == 0
    total = stats.seed_pixels + stats.assigned_pixels + stats.boundary_pixels
    assert total == gradient.size
    assert stats.assigned_pixels == 18
    assert stats.label_counts == {1: 16, 2: 4}
