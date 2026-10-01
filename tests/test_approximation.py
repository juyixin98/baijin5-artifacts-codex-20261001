"""Approximate-path tests: budget handling, error reporting, replay.

Contract point 3: when the full combinatorial enumeration exceeds the
budget the service must (a) say so, (b) switch to an approximation, and
(c) report the approximation method together with its error.  These tests
force small budgets and check those claims against independently computed
exact answers on the same data (produced with a large budget).
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.stats.contracts import ComputationKind, TwoSidedMethod
from app.stats.estimator import ApproximationUnavailable, RandomizationKernel, _Z95

ABS = TwoSidedMethod.ABS
PROB = TwoSidedMethod.PROB

# n=18 -> 262,144 assignments, comfortably above a 16-assignment budget.
D18 = [3, -1, 4, -1, 5, -9, 2, -6, 5, -3, 5, -8, 9, -7, 9, -3, 2, -3]


def _exact_settings(tmp_path) -> Settings:
    import pathlib

    return Settings(
        db_path=pathlib.Path(tmp_path) / "ex.db",
        exact_budget=10_000_000,
        crossing_budget=10_000_000,
        mc_draws=40_000,
        mc_seed=42,
        inversion_grid=3_000,
        inversion_refine=38,
    )


def test_over_budget_pvalue_is_flagged_approximate_with_error(small_budget_settings):
    r = RandomizationKernel(D18, ABS, small_budget_settings).p_value(0.0)
    assert r.kind is ComputationKind.APPROXIMATE
    assert r.n_evaluated == small_budget_settings.mc_draws + 1
    assert r.standard_error is not None and r.standard_error > 0
    assert r.monte_carlo_error == pytest.approx(_Z95 * r.standard_error)
    assert any("exceeds budget" in u for u in r.uncertainty)


def test_monte_carlo_pvalue_is_unbiased_and_close_to_exact(tmp_path, small_budget_settings):
    approx = RandomizationKernel(D18, ABS, small_budget_settings).p_value(0.0).p_value
    exact = RandomizationKernel(D18, ABS, _exact_settings(tmp_path)).p_value(0.0).p_value
    # with 40k draws the MC estimate must land within a generous 5-SE band
    se = (approx * (1 - approx) / small_budget_settings.mc_draws) ** 0.5
    assert abs(approx - exact) < 5 * se


def test_approximate_pvalue_replays_identically(small_budget_settings):
    a = RandomizationKernel(D18, ABS, small_budget_settings).p_value(0.37)
    b = RandomizationKernel(D18, ABS, small_budget_settings).p_value(0.37)
    assert a.p_value == b.p_value and a.n_evaluated == b.n_evaluated


def test_approximate_inversion_reports_non_certified_and_boundary_error(
    tmp_path, small_budget_settings
):
    inv = RandomizationKernel(D18, ABS, small_budget_settings).invert(0.1)
    assert inv.kind is ComputationKind.APPROXIMATE
    assert inv.certified is False
    assert inv.boundary_tolerance is not None and inv.boundary_tolerance > 0
    assert any("not certified exhaustive" in u for u in inv.uncertainty)

    exact = RandomizationKernel(D18, ABS, _exact_settings(tmp_path)).invert(0.1)
    assert len(inv.components) == len(exact.components) == 1
    a, e = inv.components[0], exact.components[0]
    # reported error margin must actually cover the deviation from exact
    assert abs(a.lower - e.lower) <= inv.boundary_tolerance
    assert abs(a.upper - e.upper) <= inv.boundary_tolerance


def test_prob_crossing_budget_forces_grid_but_keeps_components_separate(tmp_path):
    # Enumeration feasible (32 assignments) but exact PROB inversion's 496
    # pairwise crossings exceed crossing_budget -> grid inversion using
    # exact p-values. The disconnected structure here lives at two isolated
    # reject points (-10, -7); a grid cannot certify isolated points, so the
    # honest contract is: report non-certified and keep the broad accepted
    # region rather than silently claiming a wrong certified interval.
    grid_settings = Settings(
        db_path=tmp_path / "grid.db",
        exact_budget=4_096,
        crossing_budget=100,
        mc_draws=40_000,
        mc_seed=42,
        inversion_grid=3_000,
        inversion_refine=38,
    )
    d = [-1, -4, 2, -4, -1]
    inv = RandomizationKernel(d, PROB, grid_settings).invert(0.1)
    assert inv.kind is ComputationKind.APPROXIMATE
    assert inv.certified is False
    assert isinstance(inv.components, tuple)
    assert any("isolated accepted/rejected points" in u for u in inv.uncertainty)
    # the broad accepted interior of the exact set must still be covered
    interior = [-8.5, 0.0]
    for t in interior:
        assert any(
            (c.lower is None or c.lower < t) and (c.upper is None or c.upper > t)
            for c in inv.components
        )


def test_prob_over_enumeration_budget_is_refused_not_silently_biased(
    small_budget_settings,
):
    # 2**18 = 262144 > exact budget; probability ordering cannot be honestly
    # Monte-Carlo approximated, so the kernel refuses.
    kernel = RandomizationKernel(D18, PROB, small_budget_settings)
    with pytest.raises(ApproximationUnavailable):
        kernel.p_value(0.0)
    with pytest.raises(ApproximationUnavailable):
        kernel.invert(0.1)


def test_prob_over_budget_returns_structured_failure_via_service(small_budget_settings):
    from app.evidence import EvidenceRecord
    from app.service import InferenceService

    svc = InferenceService(small_budget_settings)
    treated, control = D18, [0] * len(D18)
    ev = EvidenceRecord()
    assert svc.p_value({"treated": treated, "control": control, "method": "two_sided_prob"}, ev) is None
    assert [f.code for f in ev.failures] == ["approximation_unresolved"]
    assert ev.failures[0].detail["suggested_method"] == "two_sided_abs"
