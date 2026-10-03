"""Residual reporting: non-additive input is allowed, fit error is reported
and cross-checked independently from the emitted Newick."""

from __future__ import annotations

import math

import pytest

from njtree import BuildParams, TreeService

from . import independent
from .conftest import load_fixture

TOL = 1e-9


def test_noisy_matrix_residual_is_nonzero_and_independently_confirmed():
    fx = load_fixture("noisy6.json")
    service = TreeService()
    result = service.build_from_matrix(fx["labels"], fx["matrix"], BuildParams())
    assert result.residuals.total_absolute > fx["expected"]["residual_must_exceed"]
    paths = independent.leaf_path_lengths(independent.parse_newick(result.newick))
    independent_total = independent.total_residual(fx["labels"], fx["matrix"], paths)
    assert result.residuals.total_absolute == pytest.approx(independent_total, abs=TOL)


def test_residual_report_structure_and_rmse():
    fx = load_fixture("noisy6.json")
    service = TreeService()
    result = service.build_from_matrix(fx["labels"], fx["matrix"], BuildParams())
    report = result.residuals
    n = len(fx["labels"])
    assert len(report.pairs) == n * (n - 1) // 2
    for pair in report.pairs:
        assert pair.residual == pytest.approx(pair.input_distance - pair.fitted_distance)
    expected_rmse = math.sqrt(sum(p.residual ** 2 for p in report.pairs) / len(report.pairs))
    assert report.rmse == pytest.approx(expected_rmse)
    assert report.max_absolute == max(abs(p.residual) for p in report.pairs)
    assert report.total_absolute == pytest.approx(sum(abs(p.residual) for p in report.pairs))


def test_additive_matrix_reports_zero_residual():
    fx = load_fixture("additive6.json")
    service = TreeService()
    result = service.build_from_matrix(fx["labels"], fx["matrix"], BuildParams())
    assert result.residuals.total_absolute == pytest.approx(0.0, abs=TOL)
    assert result.residuals.max_absolute == pytest.approx(0.0, abs=TOL)


def test_noisy_topology_still_recovered():
    # Small deterministic noise must not change the unrooted topology.
    fx = load_fixture("noisy6.json")
    service = TreeService()
    result = service.build_from_matrix(fx["labels"], fx["matrix"], BuildParams())
    inferred = independent.splits(independent.parse_newick(result.newick))
    true = independent.splits(independent.parse_newick(fx["expected"]["true_tree_newick"]))
    assert inferred == true
