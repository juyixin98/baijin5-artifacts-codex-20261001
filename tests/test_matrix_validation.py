"""Matrix validation boundary: declared check order, distinct error categories."""

from __future__ import annotations

import pytest

from njtree.errors import (
    ErrorCategory,
    InputValidationError,
    ResourceExhaustedError,
)
from njtree.matrix import CHECK_ORDER, collect_violations, validate_distance_matrix

from .conftest import load_fixture


def test_valid_matrix_passes():
    fx = load_fixture("additive4.json")
    dm = validate_distance_matrix(fx["labels"], fx["matrix"])
    assert dm.n == 4
    assert dm.labels == tuple(fx["labels"])


@pytest.mark.parametrize("labels,values,check", [
    (["A", "B", "C"], [[0, 1], [1, 0]], "shape"),                       # not square
    (["A", "B", "C"], [[0, 1, 2], [1, 0, 3], [2, 3, 0]], None),          # valid baseline
    (["A", "B", "C"], [[0, -1, 2], [-1, 0, 3], [2, 3, 0]], "non_negative"),
    (["A", "B", "C"], [[1, 1, 2], [1, 0, 3], [2, 3, 0]], "zero_diagonal"),
    (["A", "B", "C"], [[0, 1, 2], [9, 0, 3], [2, 3, 0]], "symmetric"),
    (["A", "B", "C"], [[0, float("nan"), 2], [1, 0, 3], [2, 3, 0]], "finite"),
    (["A", "B", "C"], [[0, float("inf"), 2], [1, 0, 3], [2, 3, 0]], "finite"),
    (["A", "A", "C"], [[0, 1, 2], [1, 0, 3], [2, 3, 0]], "labels"),
    (["A", "B B", "C"], [[0, 1, 2], [1, 0, 3], [2, 3, 0]], "labels"),
    (["A", "", "C"], [[0, 1, 2], [1, 0, 3], [2, 3, 0]], "labels"),
    (["A", "B"], [[0, 1], [1, 0]], "min_taxa"),
    (["A", "B", "C"], [[0, "x", 2], [1, 0, 3], [2, 3, 0]], "shape"),
])
def test_single_violation(labels, values, check):
    if check is None:
        validate_distance_matrix(labels, values)
        return
    with pytest.raises(InputValidationError) as excinfo:
        validate_distance_matrix(labels, values)
    assert excinfo.value.category is ErrorCategory.INPUT_ERROR
    assert excinfo.value.details["check"] == check


def test_too_many_taxa_is_resource_exhausted():
    labels = [f"t{i}" for i in range(5)]
    values = [[0.0 if i == j else 1.0 for j in range(5)] for i in range(5)]
    with pytest.raises(ResourceExhaustedError) as excinfo:
        validate_distance_matrix(labels, values, max_taxa=4)
    assert excinfo.value.category is ErrorCategory.RESOURCE_EXHAUSTED
    assert excinfo.value.details["check"] == "max_taxa"


def test_check_order_first_violation_wins():
    # negative entry AND asymmetric AND nonzero diagonal: non_negative is
    # declared before zero_diagonal and symmetric.
    values = [[1.0, -2.0, 3.0], [-2.0, 0.0, 4.0], [9.0, 4.0, 0.0]]
    with pytest.raises(InputValidationError) as excinfo:
        validate_distance_matrix(["A", "B", "C"], values)
    assert excinfo.value.details["check"] == "non_negative"
    all_checks = [v["check"] for v in excinfo.value.details["all_violations"]]
    assert all_checks == ["non_negative", "zero_diagonal", "symmetric"]


def test_collect_violations_reports_all_applicable():
    violations = collect_violations(["A", "B", "C"], [[1, 2, 3], [2, 0, 4], [9, 4, 0]])
    assert [v["check"] for v in violations] == ["zero_diagonal", "symmetric"]


def test_shape_error_masks_later_checks():
    # A shape violation makes finite/non-negative/symmetry uncheckable; only
    # the shape violation is reported.
    violations = collect_violations(["A", "B", "C"], [[0, 1], [1, 0]])
    assert [v["check"] for v in violations] == ["shape"]


def test_declared_check_order_matches_implementation():
    assert CHECK_ORDER == (
        "labels", "min_taxa", "max_taxa", "shape",
        "finite", "non_negative", "zero_diagonal", "symmetric",
    )
