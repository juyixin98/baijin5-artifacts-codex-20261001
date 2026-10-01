"""Synthetic fixture properties and fixed-seed regression anchors.

Anchors were produced by scripts/print_reference_values.py using the named
seeds; changing the generators must be a deliberate, reviewed change that
updates these literals.
"""

import numpy as np
import pytest

from app.lord3 import run_sequence
from app.simulation import mixed_stream, null_stream


def test_null_stream_is_deterministic_and_uniform_shaped():
    a = null_stream(1000, seed=20260929)
    b = null_stream(1000, seed=20260929)
    assert a.p_values == b.p_values
    assert a.is_null == [True] * 1000
    assert min(a.p_values) >= 0.0 and max(a.p_values) <= 1.0
    # Sanity check on the uniform generator: empirical CDF at 0.5.
    frac = sum(1 for p in a.p_values if p <= 0.5) / a.n_tests
    assert 0.42 < frac < 0.58


def test_null_stream_first_four_pvalues_anchor():
    s = null_stream(200, seed=101)
    assert s.p_values[:4] == pytest.approx(
        [0.9435325056105539, 0.35942103334157316,
         0.7848054119699771, 0.5912781852294118]
    )
    assert s.hypothesis_ids[0] == "H1"


def test_null_stream_complete_null_fixed_run_has_no_rejection_anchor():
    s = null_stream(200, seed=101)
    result = run_sequence(s.hypothesis_ids, s.p_values)
    assert result.n_rejections == 0
    assert result.final_wealth == pytest.approx(0.004028614771012739)


def test_mixed_stream_is_deterministic_with_fixed_labels():
    a = mixed_stream(200, 0.2, seed=202, beta_a=0.05)
    b = mixed_stream(200, 0.2, seed=202, beta_a=0.05)
    assert a.p_values == b.p_values and a.is_null == b.is_null
    assert a.n_nonnull == 40 and a.n_null == 160


def test_mixed_stream_nonnull_positions_and_run_anchor():
    s = mixed_stream(200, 0.2, seed=202, beta_a=0.05)
    first_nonnull = [i for i, null in enumerate(s.is_null[:40]) if not null]
    assert first_nonnull[:5] == [5, 20, 26, 29, 37]
    result = run_sequence(s.hypothesis_ids, s.p_values)
    assert result.n_rejections == 25
    # Every rejection in this fixed fixture lands on a non-null p-value.
    v = sum(
        1 for d, null in zip(result.decisions, s.is_null)
        if d.rejected and null
    )
    assert v == 0
    assert result.final_wealth == pytest.approx(0.5748777730385203)


def test_mixed_stream_block_places_signal_first():
    s = mixed_stream(100, 0.3, seed=5, block=True)
    assert s.is_null[:30] == [False] * 30
    assert s.is_null[30:] == [True] * 70


def test_different_seeds_give_different_streams():
    assert null_stream(50, 1).p_values != null_stream(50, 2).p_values


@pytest.mark.parametrize(
    "call",
    [
        lambda: null_stream(n_tests=-1, seed=1),
        lambda: mixed_stream(10, 1.1, seed=1),
        lambda: mixed_stream(10, -0.1, seed=1),
        lambda: mixed_stream(10, 0.2, seed=1, beta_a=0.0),
    ],
)
def test_stream_parameters_are_validated(call):
    with pytest.raises(ValueError):
        call()


def test_beta_alternative_pvalues_are_skewed_small():
    rng = np.random.default_rng(0)
    s = mixed_stream(2000, 0.5, seed=11, beta_a=0.05)
    nonnull_ps = [p for p, null in zip(s.p_values, s.is_null) if not null]
    assert np.median(nonnull_ps) < 0.01
