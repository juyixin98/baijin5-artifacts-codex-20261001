"""Tests for the independent finite-difference verifier.

These tests deliberately inject *wrong* analytic gradients and assert the
exact failure category, so "the interface is callable" can never masquerade
as a pass.
"""
from __future__ import annotations

import numpy as np
import pytest

from autodiff import ACCEPTED, REJECTED, UNABLE
from autodiff.diagnostics import Diagnostics
from autodiff.config import Config
from autodiff.numeric import (
    check_gradients,
    GRAD_VALUE_MISMATCH,
    GRAD_SHAPE_MISMATCH,
    NONFINITE_ANALYTIC,
    NONFINITE_REFERENCE,
)


@pytest.fixture
def diag():
    return Diagnostics(request_id="num-test", config=Config(log_diagnostics=False))


def test_accepts_correct_quadratic(diag):
    x0 = np.array([0.5, -1.0, 2.0])

    def f(d):
        return float(np.sum(d["x"] ** 2) + 3.0 * np.sum(d["x"]))

    analytic = {"x": 2.0 * x0 + 3.0}
    result = check_gradients(analytic, f, {"x": x0},
                             eps=1e-4, diagnostics=diag)
    assert result.status == ACCEPTED
    assert result.all_accepted()
    assert {leaf.name for leaf in result.leaves} == {"x.x"}
    assert result.leaves[0].max_abs_err < 1e-7


def test_rejects_wrong_gradient_with_value_category(diag):
    x0 = np.array([1.0, 2.0])

    def f(d):
        return float(np.sum(d["x"] ** 2))

    # Correct would be 2*x = [2,4]; supply [2,5].
    result = check_gradients({"x": np.array([2.0, 5.0])}, f, {"x": x0},
                             eps=1e-4, diagnostics=diag)
    assert result.status == REJECTED
    bad = [l for l in result.leaves if l.status == REJECTED]
    assert len(bad) == 1
    assert bad[0].category == GRAD_VALUE_MISMATCH
    # The wrong component is identified by the worst-index field.
    assert bad[0].worst_index == (1,)
    assert bad[0].max_abs_err > 0.5


def test_rejects_shape_mismatch_category(diag):
    x0 = np.zeros((2, 3))

    def f(d):
        return float(d["x"].sum())

    result = check_gradients({"x": np.zeros((2, 2))}, f, {"x": x0},
                             diagnostics=diag)
    assert result.status == REJECTED
    assert result.leaves[0].category == GRAD_SHAPE_MISMATCH
    # Shape mismatch never runs a perturbation (inf error, no FD cost).
    assert np.isinf(result.leaves[0].max_abs_err)


def test_rejects_nonfinite_analytic_category(diag):
    x0 = np.array([1.0, 2.0])

    def f(d):
        return float(d["x"].sum())

    result = check_gradients(
        {"x": np.array([1.0, np.nan])}, f, {"x": x0}, diagnostics=diag
    )
    assert result.status == REJECTED
    assert result.leaves[0].category == NONFINITE_ANALYTIC


def test_unable_when_reference_nonfinite(diag):
    x0 = np.array([0.5])

    def f(d):
        # log becomes -inf when perturbed below zero; .sum() keeps it scalar.
        return float(np.log(d["x"]).sum())

    result = check_gradients({"x": np.array([2.0])}, f, {"x": x0},
                             eps=1.0, diagnostics=diag)
    assert result.status == UNABLE
    assert result.leaves[0].category == NONFINITE_REFERENCE


def test_empty_leaf_accepted_on_shape_without_perturbation(diag):
    x0 = np.zeros((0, 4))

    def f(d):
        return float(d["x"].sum())

    result = check_gradients({"x": np.zeros((0, 4))}, f, {"x": x0},
                             diagnostics=diag)
    assert result.status == ACCEPTED
    assert result.leaves[0].max_abs_err == 0.0


def test_multiple_structure_leaves(diag):
    base = {"a": np.array([1.0]), "b": np.array([2.0, 3.0])}

    def f(d):
        return float(d["a"].sum() * 2.0 + d["b"].sum() * 5.0)

    analytic = {"a": np.array([2.0]), "b": np.array([5.0, 5.0])}
    result = check_gradients(analytic, f, base, eps=1e-4, diagnostics=diag)
    assert result.status == ACCEPTED
    assert {l.name for l in result.leaves} == {"x.a", "x.b"}


def test_structure_mismatch_rejected(diag):
    def f(d):
        return float(d["x"].sum())

    result = check_gradients(
        {"x": np.zeros(2)}, f, {"y": np.zeros(2)}, diagnostics=diag
    )
    assert result.status == REJECTED
    assert any(r.event == "gradcheck.structure" for r in diag.records)


def test_bad_eps_is_unable(diag):
    result = check_gradients(
        {"x": np.zeros(1)}, lambda d: 0.0, {"x": np.zeros(1)},
        eps=-1e-3, diagnostics=diag,
    )
    assert result.status == UNABLE


def test_reference_is_independent_of_core():
    # Structural guarantee: the reference function for a built-in fixture must
    # compute the same value as the core forward at the base point while never
    # importing autodiff modules (enforced by source inspection).
    import inspect

    from autodiff.fixtures import SCENARIOS

    for name, scenario in SCENARIOS.items():
        src = inspect.getsource(scenario.reference)
        assert "autodiff" not in src, f"{name} reference must be autodiff-free"
        built, reference = scenario.run()
        ref_value = reference({k: v.data.copy() for k, v in built.leaves.items()})
        assert ref_value == pytest.approx(float(built.loss.data), rel=1e-9), name
