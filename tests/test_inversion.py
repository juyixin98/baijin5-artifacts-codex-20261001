"""Confidence-set inversion tests.

Covers contract point #2: the inversion uses the same two-sided statistic as
the test, and a non-contiguous acceptance set is reported as a union of
intervals rather than forced into a single interval.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from app.core import kernel
from app.core.intervals import Interval, IntervalSet, intervals_from_mask
from app.evidence import oracle_acceptance, oracle_pvalue


# ---------------------------------------------------------------------------
# Exact inversion: hand-verified endpoints
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("d,alpha,lower,upper", [
    # balanced (1,-1,1), alpha=0.5: count>=4 on [-1,1]
    ([1, -1, 1], 0.5, -1.0, 1.0),
    # monotone (1,2,3), alpha=0.5: count>=4 on [1,3]
    ([1, 2, 3], 0.5, 1.0, 3.0),
    # monotone (1,2,3), alpha=0.75: count>=6 on [1.5,2.5]
    ([1, 2, 3], 0.75, 1.5, 2.5),
    # (1,1,2,2), alpha=0.25: count>=4 on [1,2]
    ([1, 1, 2, 2], 0.25, 1.0, 2.0),
    # (3,-1,2,2), alpha=0.5: count>=8 on [0.5,2.5]
    ([3, -1, 2, 2], 0.5, 0.5, 2.5),
    # five-pair case alpha=0.1: count>=4 on [-4,5]
    ([1, -2, 3, -4, 5], 0.1, -4.0, 5.0),
])
def test_exact_inversion_endpoints(d, alpha, lower, upper):
    iset = kernel.invert_confidence_set_exact(np.asarray(d, float), alpha)
    assert iset.n_intervals == 1
    iv = iset.intervals[0]
    assert iv.lower == pytest.approx(lower)
    assert iv.upper == pytest.approx(upper)
    assert iv.lower_inclusive and iv.upper_inclusive
    assert iset.grid_resolution is None  # exact: no grid discretization


def test_identical_outcomes_inversion_is_singleton_or_all():
    # All differences zero: p==1 only at tau=0, otherwise p=2/2^n.
    d = np.zeros(4)
    full = kernel.invert_confidence_set_exact(d, alpha=0.05)
    assert full.n_intervals == 1
    assert not math.isfinite(full.intervals[0].lower)
    assert not math.isfinite(full.intervals[0].upper)

    singleton = kernel.invert_confidence_set_exact(d, alpha=0.5)
    assert singleton.n_intervals == 1
    iv = singleton.intervals[0]
    assert iv.is_singleton and iv.lower == 0.0
    # Points arbitrarily close but not zero are rejected.
    assert not singleton.contains(1e-9)


def test_extreme_differences_inversion():
    d = np.array([5.0, -5.0, 5.0, -5.0])
    iset = kernel.invert_confidence_set_exact(d, alpha=0.25)
    assert [(iv.lower, iv.upper) for iv in iset.intervals] == [(-5.0, 5.0)]
    narrow = kernel.invert_confidence_set_exact(d, alpha=0.75)
    assert narrow.intervals[0].lower == pytest.approx(-5 / 3)
    assert narrow.intervals[0].upper == pytest.approx(5 / 3)


# ---------------------------------------------------------------------------
# Exact inversion matches the independent oracle pointwise
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("d", [
    [0, 0, 0, 0], [1, -1, 1], [5, -5, 5, -5], [1, 2, 3],
    [3, -1, 2, 2], [1, -2, 3, -4, 5], [0.5, -1.25, 2.0, 0.0, 0.75],
])
@pytest.mark.parametrize("alpha", [0.05, 0.1, 0.25, 0.5, 0.75])
def test_exact_inversion_membership_matches_oracle(d, alpha):
    d = np.asarray(d, dtype=float)
    iset = kernel.invert_confidence_set_exact(d, alpha)
    grid = np.linspace(-8, 8, 3201)
    expected = oracle_acceptance(d, alpha, grid)
    mismatches = [
        float(t) for t, want in zip(grid, expected)
        if iset.contains(float(t)) != bool(want)
    ]
    assert mismatches == []


def test_every_exact_set_contains_mean_point():
    # Structural property of this two-sided statistic: the observed signed
    # residual sum vanishes at tau = mean(d), where p=1, so the acceptance
    # set always contains that point.
    rng = np.random.default_rng(0)
    for _ in range(20):
        d = rng.choice([-3, -2, -1, 0, 1, 2, 3], size=6)
        mean = float(d.mean())
        for alpha in (0.05, 0.25, 0.5, 0.9):
            iset = kernel.invert_confidence_set_exact(d, alpha)
            assert iset.contains(mean)
            assert kernel.exact_pvalue(d, mean).p_value == pytest.approx(1.0)


def test_exact_pvalue_profile_consistent_with_inversion():
    d = np.array([3.0, -1.0, 2.0, 2.0])
    alpha = 0.5
    grid = np.linspace(-2, 4, 1201)
    profile = kernel.exact_pvalue_profile(d, grid)
    iset = kernel.invert_confidence_set_exact(d, alpha)
    for tau, p in zip(grid, profile):
        assert iset.contains(float(tau)) == (p >= alpha - 1e-15)


# ---------------------------------------------------------------------------
# Disconnected acceptance sets must survive as a union
# ---------------------------------------------------------------------------

def test_interval_set_never_collapses_gaps():
    grid = np.linspace(0.0, 4.0, 5)  # 0,1,2,3,4
    accepted = np.array([True, False, True, False, True])
    iset = intervals_from_mask(grid, accepted)
    assert iset.n_intervals == 3
    assert [iv.lower for iv in iset.intervals] == [0.0, 2.0, 4.0]
    assert all(iv.is_singleton for iv in iset.intervals)
    assert iset.is_connected is False


def test_interval_set_preserves_large_gap_and_endpoint_groups():
    grid = np.linspace(0.0, 10.0, 11)
    accepted = np.array(
        [True, True, False, False, False, False, False, False, True, True, True]
    )
    iset = intervals_from_mask(grid, accepted)
    assert iset.n_intervals == 2
    assert iset.intervals[0] == Interval(0.0, 1.0)
    assert iset.intervals[1] == Interval(8.0, 10.0)
    # Gap points stay rejected; interior points of components stay accepted.
    assert not iset.contains(4.0)
    assert iset.contains(0.5) is True
    assert not iset.contains(5.0)


def test_interval_set_serializes_components():
    iset = IntervalSet(intervals=(Interval(-1.0, 0.0), Interval(2.0, 3.0)))
    payload = iset.to_dict()
    assert payload["n_intervals"] == 2
    assert payload["is_connected"] is False
    assert [c["lower"] for c in payload["intervals"]] == [-1.0, 2.0]


# ---------------------------------------------------------------------------
# Monte-Carlo inversion: genuinely approximate, error reported
# ---------------------------------------------------------------------------

def test_mc_inversion_recovers_exact_set_within_tolerance():
    # n=10 is exactly enumerable (1024), which gives us a reference to judge
    # the grid + Monte-Carlo approximation.
    d = np.array([1.0, -2.0, 0.5, 3.0, -1.0, 2.0, 0.0, -3.0, 1.5, -0.5])
    alpha = 0.1
    exact = kernel.invert_confidence_set_exact(d, alpha)
    grid = np.linspace(-6, 6, 4801)
    iset, p_values, halfwidth = kernel.mc_invert_confidence_set(
        d, alpha=alpha, n_draws=20_000, grid=grid, seed=7
    )
    assert iset.n_intervals >= 1
    assert halfwidth > 0.0
    # The MC endpoints must bracket/agree with the exact endpoints within a
    # tolerance accounting for grid step (~0.0025) plus MC noise.
    ex_lo, ex_hi = exact.intervals[0].lower, exact.intervals[0].upper
    mc_lo = min(iv.lower for iv in iset.intervals)
    mc_hi = max(iv.upper for iv in iset.intervals)
    assert abs(mc_lo - ex_lo) < 0.05
    assert abs(mc_hi - ex_hi) < 0.05
    # Grid resolution and the approximation note are reported, not hidden.
    assert iset.grid_resolution == pytest.approx(12 / 4800)
    assert "Monte-Carlo" in iset.note


def test_mc_inversion_is_seed_deterministic():
    d = np.array([float((i % 5) - 2) for i in range(24)])
    grid = np.linspace(-5, 5, 201)
    set_a, p_a, _ = kernel.mc_invert_confidence_set(
        d, 0.05, n_draws=500, grid=grid, seed=11
    )
    set_b, p_b, _ = kernel.mc_invert_confidence_set(
        d, 0.05, n_draws=500, grid=grid, seed=11
    )
    assert np.array_equal(p_a, p_b)
    assert set_a.intervals == set_b.intervals
