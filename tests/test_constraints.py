"""Constraint unit tests: step legality, slope throttling, window bounds."""

import pytest

from dtw_service.constraints import PathConstraints, check_path_legal


def test_unbounded_horizontal_run_is_illegal():
    constraints = PathConstraints(window=10, max_run=2)
    # Three consecutive horizontal steps exceed max_run=2.
    path = [(0, 0), (1, 0), (2, 0), (3, 0), (3, 1), (4, 2)]
    assert not check_path_legal(path, 5, 3, constraints)


def test_alternating_runs_within_limit_are_legal():
    constraints = PathConstraints(window=10, max_run=2)
    path = [(0, 0), (1, 0), (2, 0), (2, 1), (3, 1), (4, 1), (4, 2)]
    assert check_path_legal(path, 5, 3, constraints)


def test_backward_and_diagonal_skip_steps_are_illegal():
    constraints = PathConstraints(window=10, max_run=2)
    assert not check_path_legal([(0, 0), (0, 1), (0, 0), (1, 1)], 2, 2, constraints)
    assert not check_path_legal([(0, 0), (2, 2)], 3, 3, constraints)


def test_window_bounds_endpoint_and_cells():
    constraints = PathConstraints(window=1, max_run=2)
    assert constraints.endpoint_possible(5, 5)
    assert not constraints.endpoint_possible(5, 8)  # |4-7| = 3 > 1
    assert not check_path_legal([(0, 0), (0, 1), (0, 2), (1, 2)], 2, 3, constraints)


def test_path_must_span_origin_to_endpoint():
    constraints = PathConstraints(window=10, max_run=2)
    assert not check_path_legal([(0, 0), (1, 1)], 3, 3, constraints)
    assert not check_path_legal([(1, 1), (2, 2)], 3, 3, constraints)


def test_invalid_constraints_rejected():
    with pytest.raises(ValueError):
        PathConstraints(window=-1, max_run=2)
    with pytest.raises(ValueError):
        PathConstraints(window=1, max_run=0)
