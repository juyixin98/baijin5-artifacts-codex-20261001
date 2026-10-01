"""Exact kernel tests: p-values and acceptance-set inversion.

Concrete expected values are hand-derived (and independently reproduced by
``tests/reference_oracle.py``, which never imports the application code).
"""

from __future__ import annotations

import math

import pytest

from app.stats.contracts import ComputationKind, TwoSidedMethod
from app.stats.estimator import RandomizationKernel
from reference_oracle import exact_acceptance_set, exact_p_value

ABS = TwoSidedMethod.ABS
PROB = TwoSidedMethod.PROB


# ---------------------------------------------------------------------------
# Hand-derived p-value fixtures
# ---------------------------------------------------------------------------
def test_pvalue_abs_is_hand_enumerated_for_three_pairs(settings):
    # d=[1,2,3]; observed S=6; the 8 null stats are
    # {6,4,2,0,0,-2,-4,-6}; |v|>=6 occurs twice -> p = 2/8.
    k = RandomizationKernel([1, 2, 3], ABS, settings)
    r = k.p_value(0.0)
    assert r.kind is ComputationKind.EXACT
    assert r.n_extreme == 2
    assert r.randomization_set_size == 8
    assert r.p_value == pytest.approx(0.25)


def test_pvalue_prob_uses_mass_ordering_not_absolute_value(settings):
    # Same stats {6,4,2,0,0,-2,-4,-6}; the observed value 6 has atom mass 1,
    # so every mass-1 value is "as extreme": 6 of 8 -> p = 3/4.
    k = RandomizationKernel([1, 2, 3], PROB, settings)
    r = k.p_value(0.0)
    assert r.kind is ComputationKind.EXACT
    assert r.n_extreme == 6
    assert r.p_value == pytest.approx(0.75)


def test_pvalue_one_when_observed_statistic_is_zero(settings):
    # d=[1,-1], n=2; obs S(0)=0, so under |.| ordering all 4 stats qualify.
    k = RandomizationKernel([1, -1], ABS, settings)
    r = k.p_value(0.0)
    assert r.n_extreme == 4
    assert r.p_value == pytest.approx(1.0)


def test_identical_outcomes_never_reject_and_set_is_whole_line(settings):
    d = [0.0, 0.0, 0.0, 0.0]
    for method in (ABS, PROB):
        k = RandomizationKernel(d, method, settings)
        assert k.p_value(0.0, alpha=0.05).rejected is False
        res = k.invert(0.05)
        assert res.certified is True
        assert len(res.components) == 1
        c = res.components[0]
        assert c.lower is None and c.upper is None  # the whole real line


def test_extreme_differences_pvalue_one(settings):
    # d=[10,-10,10,-10], obs S(0)=0 -> all 16 assignments at least as extreme.
    k = RandomizationKernel([10, -10, 10, -10], ABS, settings)
    assert k.p_value(0.0).p_value == pytest.approx(1.0)


def test_constant_effect_shift_moves_null_distribution(settings):
    # Under a true constant effect the mean difference is a natural centre;
    # p(tau = mean d) must be 1 while a far-away tau is rejected.  n=6 is
    # needed because the smallest attainable two-sided p for n pairs is
    # 2/2**n (only the two extreme assignments), i.e. ~0.031 for n=6.
    d = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    k = RandomizationKernel(d, ABS, settings)
    assert k.p_value(3.5).p_value == pytest.approx(1.0)
    assert k.p_value(100.0, alpha=0.05).rejected is True


# ---------------------------------------------------------------------------
# Inversion: same two-sided definition, components kept separate
# ---------------------------------------------------------------------------
def test_abs_inversion_set_for_three_pairs(settings):
    # Hand/independent-oracle result: {tau : p_abs(tau) > .25} = [1, 3].
    k = RandomizationKernel([1, 2, 3], ABS, settings)
    res = k.invert(0.25)
    assert res.certified is True
    assert res.is_disconnected is False
    assert len(res.components) == 1
    c = res.components[0]
    assert (c.lower, c.upper) == (pytest.approx(1.0), pytest.approx(3.0))
    assert c.lower_closed and c.upper_closed


def test_prob_inversion_is_genuinely_disconnected(settings):
    # d=[-1,-4,2,-4,-1], n=5, alpha=.1, probability ordering.
    # Exact acceptance set (independent Fraction oracle):
    #   (-inf,-10) U (-10,-7) U (-7,+inf), all endpoints open.
    d = [-1, -4, 2, -4, -1]
    k = RandomizationKernel(d, PROB, settings)
    res = k.invert(0.1)
    assert res.certified is True
    assert res.is_disconnected is True
    assert len(res.components) == 3
    lo_a, mid, hi_b = res.components
    assert lo_a.lower is None and lo_a.upper == pytest.approx(-10.0) and not lo_a.upper_closed
    assert (mid.lower, mid.upper) == (pytest.approx(-10.0), pytest.approx(-7.0))
    assert not mid.lower_closed and not mid.upper_closed
    assert hi_b.lower == pytest.approx(-7.0) and hi_b.upper is None and not hi_b.lower_closed

    # the two boundary points themselves are rejected, the gap interior is kept
    assert k.p_value(-10.0, alpha=0.1).rejected is True
    assert k.p_value(-7.0, alpha=0.1).rejected is True
    assert k.p_value(-8.5, alpha=0.1).rejected is False
    # zero effect p-values under the two definitions differ and are exact
    assert k.p_value(0.0).p_value == pytest.approx(7 / 16)
    assert RandomizationKernel(d, ABS, settings).p_value(0.0).p_value == pytest.approx(5 / 16)

    # the hull is exposed only as labelled metadata and is NOT the returned set
    assert res.hull is not None and res.hull.lower is None and res.hull.upper is None
    assert [c.render() for c in res.components] != [res.hull.render()]


def test_abs_and_prob_inversion_agree_when_orderings_coincide(settings):
    # For d=[1,2,3] at alpha .05 both definitions keep the whole line.
    for method in (ABS, PROB):
        res = RandomizationKernel([1, 2, 3], method, settings).invert(0.05)
        assert len(res.components) == 1
        assert res.components[0].lower is None and res.components[0].upper is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
def test_kernel_rejects_non_finite_differences(settings):
    with pytest.raises(ValueError):
        RandomizationKernel([1.0, float("inf")], ABS, settings)


def test_exact_inversion_has_no_one_sided_epsilon(settings):
    # Regression: acceptance is the EXACT integer test extreme/size > alpha.
    # With alpha = (attainable 2/8) - 1e-12 the tails (p exactly 0.25) must
    # still be accepted; an earlier "p > alpha + 1e-10" rule wrongly dropped
    # them. Independent Fraction oracle gives the target set.
    alpha = 0.25 - 1e-12
    res = RandomizationKernel([1, 2, 3], ABS, settings).invert(alpha)
    ref = exact_acceptance_set([1, 2, 3], alpha, ABS.value)
    assert len(res.components) == len(ref)
    for c, (lo, _, hi, _) in zip(res.components, ref):
        assert (c.lower is None) == (lo is None)
        assert (c.upper is None) == (hi is None)
        if lo is not None:
            assert c.lower == pytest.approx(float(lo), abs=1e-9)
        if hi is not None:
            assert c.upper == pytest.approx(float(hi), abs=1e-9)


def test_atom_clustering_does_not_chain_distinct_values():
    import numpy as np

    from app.stats.estimator import _cluster_masses

    # Three genuinely distinct, progressively-spaced values must not be
    # single-linkage-chained into one phantom atom.
    masses = _cluster_masses(np.array([0.0, 6e-10, 1.2e-9]))
    assert masses.tolist() == [1, 1, 1]
    # genuinely equal values still form one atom
    assert set(_cluster_masses(np.array([0.1, 0.1, 0.1, 0.5])).tolist()) == {3, 3, 3, 1}


def test_abs_extreme_count_does_not_inflate_across_a_gap(settings):
    import numpy as np

    from app.stats.estimator import _extreme_count_abs

    # observed magnitude 1.0; a value smaller by 5e-10 is NOT a tie and must
    # not be counted (an earlier one-sided shifted threshold counted it).
    stat = np.array([1.0, 1.0 - 5e-10, 1.2, 0.3])
    assert _extreme_count_abs(stat, 0) == 2  # exactly 1.0 (obs) and 1.2


def test_pooled_se_uses_correct_variance_factor():
    from app.stats.estimator import _pooled_se

    p, m_total = 0.4, 10_001  # 10_000 draws + observed
    expected = (p * (1 - p) * (10_000 / 10_001) / 10_001) ** 0.5
    assert _pooled_se(p, m_total) == pytest.approx(expected, rel=1e-12)
    assert _pooled_se(p, m_total) < (p * (1 - p) / m_total) ** 0.5


def test_abs_acceptance_set_always_contains_mean_difference(settings):
    # At tau = mean(d) the observed statistic S_obs = 0, so every assignment
    # is "as extreme" and p = 1: that tau can never be rejected.
    import numpy as np

    from app.stats.estimator import _hull

    for d in ([1, -1], [1, 2, 3], [2, -5, 3, 7, -2, 4]):
        mean = float(np.mean(d))
        res = RandomizationKernel(d, ABS, settings).invert(0.5)
        assert any(
            (c.lower is None or c.lower <= mean) and (c.upper is None or c.upper >= mean)
            for c in res.components
        )
    assert _hull(()) is None  # empty union has no hull


# ---------------------------------------------------------------------------
# Exhaustive agreement with the independent Fraction oracle
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(12))
def test_kernel_matches_independent_fraction_oracle(seed, settings):
    import random

    rng = random.Random(1000 + seed)
    n = rng.choice([2, 3, 4, 5])
    d = [rng.randint(-5, 5) for _ in range(n)]
    for method in (ABS, PROB):
        k = RandomizationKernel(d, method, settings)
        for tau in (-1.0, 0.0, 0.5, 2.0):
            assert k.p_value(tau).p_value == pytest.approx(
                float(exact_p_value(d, tau, method.value)), abs=1e-12
            )
        for alpha in (0.05, 0.1, 0.25):
            res = k.invert(alpha)
            ref = exact_acceptance_set(d, alpha, method.value)
            assert len(res.components) == len(ref)
            for c, (rlo, rlc, rhi, rhc) in zip(res.components, ref):
                assert c.lower_closed == rlc and c.upper_closed == rhc
                if rlo is None:
                    assert c.lower is None
                else:
                    assert c.lower == pytest.approx(float(rlo), abs=1e-9)
                if rhi is None:
                    assert c.upper is None
                else:
                    assert c.upper == pytest.approx(float(rhi), abs=1e-9)
