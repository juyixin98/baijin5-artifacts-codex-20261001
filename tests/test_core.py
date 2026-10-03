"""Behavioral tests: speed change, local gaps, empty input, narrow windows,
monotonicity, normalization convention, and banded/dense equivalence."""

import numpy as np
import pytest

from dtw_service.constraints import PathConstraints, check_path_legal
from dtw_service.core import UnreachablePathError, dtw_align, local_distance_matrix
from dtw_service.stretch import local_stretch_rates, mean_stretch_rate

def wide(n: int, m: int) -> PathConstraints:
    """Effectively unwindowed constraints sized to the actual inputs."""
    return PathConstraints(window=max(n, m), max_run=2)


def _sine(n, freq=0.25):
    t = np.arange(n)
    return np.sin(freq * t).reshape(-1, 1)


def test_identical_sequences_give_diagonal_zero_cost():
    seq = _sine(40)
    result = dtw_align(seq, seq.copy(), wide(40, 40))
    assert result.total_cost == pytest.approx(0.0, abs=1e-12)
    assert result.path == [(i, i) for i in range(40)]
    assert result.normalized_cost == pytest.approx(0.0, abs=1e-12)


def test_speed_change_stretches_path_and_rate():
    reference = _sine(60)
    query = _sine(30, freq=0.5)  # same waveform, 2x faster: 30 frames cover 60
    constraints = PathConstraints(window=40, max_run=3)
    result = dtw_align(query, reference, constraints)

    assert check_path_legal(result.path, 30, 60, constraints)
    # Reference advances ~2 frames per query frame.
    assert mean_stretch_rate(result.path) == pytest.approx(2.0, rel=0.05)
    rates = local_stretch_rates(result.path, window_steps=4)
    finite = rates[np.isfinite(rates)]
    assert finite.mean() == pytest.approx(2.0, rel=0.1)
    # Warped match is close, so normalized cost stays small.
    assert result.normalized_cost < 0.1


def test_local_gap_is_bridged_and_endpoint_reached():
    reference = _sine(50)
    query = np.concatenate([reference[:20], reference[30:]])  # 10-frame gap
    constraints = PathConstraints(window=15, max_run=2)
    result = dtw_align(query, reference, constraints)

    assert result.path[0] == (0, 0)
    assert result.path[-1] == (len(query) - 1, len(reference) - 1)
    assert check_path_legal(result.path, len(query), len(reference), constraints)
    # The gap forces vertical runs (reference-only advance) somewhere.
    steps = np.diff(np.array(result.path), axis=0)
    assert (steps == [0, 1]).all(axis=1).any()


def test_empty_sequence_is_rejected():
    with pytest.raises(ValueError, match="non-empty"):
        dtw_align([], _sine(5), wide(1, 5))
    with pytest.raises(ValueError, match="non-empty"):
        dtw_align(_sine(5), [[]], wide(5, 1))


def test_too_narrow_window_fails_explicitly():
    a = _sine(10)
    b = _sine(30)
    # |n - m| = 20 > window, endpoint cannot lie in band.
    with pytest.raises(UnreachablePathError, match="outside the Sakoe-Chiba band"):
        dtw_align(a, b, PathConstraints(window=5, max_run=2))


def test_slope_constraint_can_make_endpoint_unreachable():
    # 1x5 vs 1x1 needs a 4-step horizontal run; max_run=1 forbids it.
    with pytest.raises(UnreachablePathError):
        dtw_align([[0.0]], [[0.0], [1.0], [2.0], [3.0], [4.0]],
                  PathConstraints(window=10, max_run=1))


def test_path_is_monotone_and_cost_matches_local_sum():
    rng = np.random.default_rng(7)
    a = rng.normal(size=(25, 3))
    b = rng.normal(size=(30, 3))
    constraints = PathConstraints(window=8, max_run=2)
    result = dtw_align(a, b, constraints)

    diffs = np.diff(np.array(result.path), axis=0)
    assert (diffs >= 0).all() and (diffs.sum(axis=1) >= 1).all()

    local = local_distance_matrix(a, b)
    recomputed = sum(local[i, j] for i, j in result.path)
    assert result.total_cost == pytest.approx(recomputed, rel=1e-10)
    # Fixed denominator convention: normalized = total / number of pairs.
    assert result.normalized_cost == pytest.approx(
        result.total_cost / result.path_length, rel=1e-12
    )


def test_banded_storage_matches_dense_storage():
    # Correlated sequences keep the optimal path near the diagonal, so the
    # banded run and the dense run must find the identical path and cost.
    rng = np.random.default_rng(11)
    a = rng.normal(size=(40, 2))
    b = a + rng.normal(scale=0.01, size=(40, 2))
    banded = dtw_align(a, b, PathConstraints(window=6, max_run=2))
    dense = dtw_align(a, b, wide(40, 40))
    assert banded.total_cost == pytest.approx(dense.total_cost, rel=1e-12)
    assert banded.path == dense.path


def test_keep_matrix_returns_accumulated_cost():
    a = _sine(8)
    b = _sine(8)
    result, dense = dtw_align(a, b, PathConstraints(window=3, max_run=2), keep_matrix=True)
    assert dense.shape == (8, 8)
    assert dense[7, 7] == pytest.approx(result.total_cost)
    assert np.isinf(dense[0, 7])  # outside the band
