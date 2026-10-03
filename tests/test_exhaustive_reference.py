"""Exhaustive verification: the DP core must match brute-force enumeration
of every legal path on short sequences, across shapes, windows and run limits."""

import numpy as np
import pytest

from dtw_service.constraints import PathConstraints, check_path_legal
from dtw_service.core import UnreachablePathError, dtw_align

from reference import best_path_cost

SHAPES = [(1, 1), (1, 3), (3, 1), (2, 3), (3, 2), (3, 3), (4, 3), (3, 4), (4, 4)]
WINDOWS = [0, 1, 2, 4]
MAX_RUNS = [1, 2, 3]


def _random_sequences(n, m, dim, seed):
    rng = np.random.default_rng(seed)
    return rng.normal(size=(n, dim)).tolist(), rng.normal(size=(m, dim)).tolist()


@pytest.mark.parametrize("n,m", SHAPES)
@pytest.mark.parametrize("window", WINDOWS)
@pytest.mark.parametrize("max_run", MAX_RUNS)
def test_core_matches_exhaustive_reference(n, m, window, max_run):
    a, b = _random_sequences(n, m, dim=2, seed=hash((n, m, window, max_run)) % 10000)
    constraints = PathConstraints(window=window, max_run=max_run)
    reference_cost = best_path_cost(a, b, window, max_run)

    if reference_cost is None:
        with pytest.raises(UnreachablePathError):
            dtw_align(a, b, constraints)
        return

    result = dtw_align(a, b, constraints)
    assert result.total_cost == pytest.approx(reference_cost, rel=1e-9, abs=1e-9)
    assert result.normalized_cost == pytest.approx(
        reference_cost / result.path_length, rel=1e-12
    )
    assert check_path_legal(result.path, n, m, constraints)
