"""Contract 1: the randomization set is strictly paired.

Expected numbers here are hand-derived, not produced by the kernel under
test.  For n pairs there must be exactly 2**n assignments, each obtained by
independently swapping the two members *within* every pair.  No outcome may
ever move to a different pair.
"""

from __future__ import annotations

import numpy as np

from app.stats.contracts import PairDesign
from app.stats.design import (
    assignment_differences,
    iter_assignments,
    sign_vectors,
    validate_observations,
)


def test_design_holds_differences_by_pair():
    design = validate_observations([10, 20, 30], [1, 2, 3])
    assert design.n_pairs == 3
    assert design.differences == (9, 18, 27)
    assert design.randomization_set_size == 8  # 2**3, never 6! = 720


def test_sign_vectors_are_one_independent_bit_per_pair():
    bits = sign_vectors(3)
    assert bits.shape == (8, 3)
    # every flip pattern occurs exactly once
    assert {tuple(int(x) for x in row) for row in bits} == {
        tuple((a >> i) & 1 for i in range(3)) for a in range(8)
    }
    # columns are independent balanced bits (not a whole-sample permutation)
    assert bits.sum(axis=0).tolist() == [4, 4, 4]


def test_assignments_only_swap_within_pair_never_across_pairs():
    treated = [10, 20, 30]
    control = [1, 2, 3]
    design = validate_observations(treated, control)
    for nt, nc in iter_assignments(design):
        for i in range(3):
            # pair i still consists of exactly its own two outcomes
            assert {nt[i], nc[i]} == {treated[i], control[i]}
        # marginal totals are invariant under within-pair flips
        assert sum(nt) + sum(nc) == sum(treated) + sum(control)


def test_assignment_differences_are_pairwise_sign_flips():
    design = validate_observations([10, 20], [1, 2])  # d = [9, 18]
    signed = assignment_differences(design)
    # rows must be the four Cartesian sign choices of [9, 18]
    rows = {tuple(int(v) for v in row) for row in signed}
    assert rows == {(9, 18), (9, -18), (-9, 18), (-9, -18)}
    # flipping pair 0 never changes the column of pair 1
    assert np.all(np.abs(signed[:, 1]) == 18)
    assert np.all(np.abs(signed[:, 0]) == 9)


def test_validation_rejects_mismatched_lengths_and_nonfinite():
    import pytest

    with pytest.raises(ValueError):
        validate_observations([1, 2], [1])
    with pytest.raises(ValueError):
        validate_observations([1, float("nan")], [1, 2])
    with pytest.raises(ValueError):
        validate_observations([1], [2])  # single pair is not a paired experiment here
