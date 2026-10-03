"""Negative branch lengths: handled per declared mode, never silently zeroed."""

from __future__ import annotations

import pytest

from njtree import BuildParams, TreeService, validate_distance_matrix
from njtree.errors import ComputationError, ErrorCategory
from njtree.models import NegativeBranchMode
from njtree.nj import neighbor_joining
from njtree.tree import to_newick

from . import independent
from .conftest import load_fixture

TOL = 1e-9


@pytest.fixture
def dm():
    fx = load_fixture("negative_branch.json")
    return validate_distance_matrix(fx["labels"], fx["matrix"])


def test_error_mode_aborts_with_computation_failure(dm):
    with pytest.raises(ComputationError) as excinfo:
        neighbor_joining(dm, mode=NegativeBranchMode.ERROR, run_id="t-neg-error")
    assert excinfo.value.category is ErrorCategory.COMPUTATION_FAILURE
    assert excinfo.value.run_id == "t-neg-error"
    assert excinfo.value.details["value"] == pytest.approx(-2.5)


def test_report_mode_keeps_negative_lengths(dm):
    fx = load_fixture("negative_branch.json")
    root, _, events = neighbor_joining(dm, mode=NegativeBranchMode.REPORT)
    assert to_newick(root) == fx["expected"]["newick_report"]
    assert [(e.step_index, e.node_label, e.original) for e in events] == [
        (e["step_index"], e["node_label"], e["original"])
        for e in fx["expected"]["negative_events"]
    ]
    assert all(e.applied == e.original for e in events)


def test_clamp_mode_records_originals_and_stays_visible(dm):
    fx = load_fixture("negative_branch.json")
    root, _, events = neighbor_joining(dm, mode=NegativeBranchMode.CLAMP)
    assert to_newick(root) == fx["expected"]["newick_clamp"]
    # Nothing silent: every clamped branch records its original estimate.
    assert [e.original for e in events] == [pytest.approx(-2.5), pytest.approx(-2.5)]
    assert [e.applied for e in events] == [0.0, 0.0]


def test_clamping_does_not_hide_fit_error(dm):
    # Hand-computed: clamping raises the total residual from 18.0 to 24.0.
    # The residual is computed against the emitted (clamped) tree, so the
    # introduced error stays visible instead of being masked.
    fx = load_fixture("negative_branch.json")
    service = TreeService()
    report = service.build_from_matrix(
        fx["labels"], fx["matrix"],
        BuildParams(negative_branch_mode=NegativeBranchMode.REPORT))
    clamp = service.build_from_matrix(
        fx["labels"], fx["matrix"],
        BuildParams(negative_branch_mode=NegativeBranchMode.CLAMP))
    assert report.residuals.total_absolute == pytest.approx(
        fx["expected"]["total_absolute_residual_report"], abs=TOL)
    assert clamp.residuals.total_absolute == pytest.approx(
        fx["expected"]["total_absolute_residual_clamp"], abs=TOL)
    assert clamp.residuals.total_absolute > report.residuals.total_absolute


def test_report_mode_residual_matches_independent_check(dm):
    fx = load_fixture("negative_branch.json")
    service = TreeService()
    result = service.build_from_matrix(
        fx["labels"], fx["matrix"],
        BuildParams(negative_branch_mode=NegativeBranchMode.REPORT))
    paths = independent.leaf_path_lengths(independent.parse_newick(result.newick))
    independent_total = independent.total_residual(fx["labels"], fx["matrix"], paths)
    assert result.residuals.total_absolute == pytest.approx(independent_total, abs=TOL)


def test_default_mode_is_report():
    assert BuildParams().negative_branch_mode is NegativeBranchMode.REPORT
