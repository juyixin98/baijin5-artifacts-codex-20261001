"""Negative branch lengths: handled per declared mode, never silently."""

from __future__ import annotations

import pytest

from app.errors import ComputationFailedError, ErrorCategory
from app.newick import serialize_newick
from app.nj import neighbor_joining
from app.residuals import compute_residuals

TOL = 1e-9


def test_allow_mode_keeps_negative_branch_and_reports_it(load_fixture):
    fx = load_fixture("negative_branch_4taxon.json")
    expected = fx["expected"]["allow"]
    result = neighbor_joining(fx["labels"], fx["matrix"], negative_branch_mode="allow")
    newick, _ = serialize_newick(result)

    assert newick == expected["newick"]
    neg = [e for e in result.events if e["type"] == "negative_branch"]
    assert len(neg) == 1
    assert neg[0]["data"]["length"] == pytest.approx(
        expected["negative_branch"]["length"], abs=TOL
    )
    # The matrix is additive *with* the negative edge: residual stays zero.
    residuals = compute_residuals(fx["labels"], fx["matrix"], result)
    assert residuals.sum_abs == pytest.approx(expected["residual_sum_abs"], abs=TOL)


def test_clamp_mode_records_correction_and_distorts_residual(load_fixture):
    fx = load_fixture("negative_branch_4taxon.json")
    expected = fx["expected"]["clamp"]
    result = neighbor_joining(fx["labels"], fx["matrix"], negative_branch_mode="clamp")
    newick, _ = serialize_newick(result)

    assert ":0," in newick or ":0)" in newick  # clamped branch emitted as 0
    clamps = [e for e in result.events if e["type"] == "branch_clamped"]
    assert len(clamps) == 1
    assert clamps[0]["data"]["raw_length"] == pytest.approx(
        expected["clamped"]["raw_length"], abs=TOL
    )
    assert clamps[0]["data"]["emitted_length"] == 0.0

    # The clamp is NOT hidden: the residual report shows the fit error.
    residuals = compute_residuals(fx["labels"], fx["matrix"], result)
    assert residuals.sum_abs == pytest.approx(expected["residual_sum_abs"], abs=TOL)
    assert residuals.max_abs == pytest.approx(expected["residual_max_abs"], abs=TOL)


def test_error_mode_aborts_with_computation_failed(load_fixture):
    fx = load_fixture("negative_branch_4taxon.json")
    with pytest.raises(ComputationFailedError) as exc:
        neighbor_joining(fx["labels"], fx["matrix"], negative_branch_mode="error")
    assert exc.value.category is ErrorCategory.COMPUTATION_FAILED
    assert exc.value.details["length"] == pytest.approx(-3.5, abs=TOL)
    assert exc.value.details["round"] == 0


def test_default_mode_is_allow(load_fixture):
    fx = load_fixture("negative_branch_4taxon.json")
    result = neighbor_joining(fx["labels"], fx["matrix"])
    assert any(e["type"] == "negative_branch" for e in result.events)
