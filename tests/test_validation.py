"""Matrix preconditions: symmetry, zero diagonal, non-negativity, resources."""

from __future__ import annotations

import pytest

from app.errors import ErrorCategory, InputValidationError, ResourceExhaustedError
from app.validation import validate_distance_matrix

LABELS = ["A", "B", "C"]
GOOD = [
    [0.0, 1.0, 2.0],
    [1.0, 0.0, 3.0],
    [2.0, 3.0, 0.0],
]


def test_valid_matrix_passes_and_is_copied():
    original = [row[:] for row in GOOD]
    result = validate_distance_matrix(LABELS, GOOD, max_taxa=10)
    assert result == GOOD
    assert GOOD == original  # input not mutated


def test_asymmetry_rejected():
    bad = [row[:] for row in GOOD]
    bad[0][1] = 1.5
    with pytest.raises(InputValidationError) as exc:
        validate_distance_matrix(LABELS, bad, max_taxa=10)
    assert "not symmetric" in exc.value.message
    assert exc.value.details["upper"] == 1.5 and exc.value.details["lower"] == 1.0


def test_nonzero_diagonal_rejected():
    bad = [row[:] for row in GOOD]
    bad[1][1] = 0.25
    with pytest.raises(InputValidationError) as exc:
        validate_distance_matrix(LABELS, bad, max_taxa=10)
    assert "diagonal" in exc.value.message


def test_negative_distance_rejected():
    bad = [row[:] for row in GOOD]
    bad[0][2] = bad[2][0] = -0.1
    with pytest.raises(InputValidationError) as exc:
        validate_distance_matrix(LABELS, bad, max_taxa=10)
    assert "negative" in exc.value.message


def test_non_finite_rejected():
    bad = [row[:] for row in GOOD]
    bad[1][2] = bad[2][1] = float("nan")
    with pytest.raises(InputValidationError) as exc:
        validate_distance_matrix(LABELS, bad, max_taxa=10)
    assert "non-finite" in exc.value.message


def test_non_square_rejected():
    with pytest.raises(InputValidationError) as exc:
        validate_distance_matrix(LABELS, [row[:2] for row in GOOD], max_taxa=10)
    assert "not square" in exc.value.message


def test_row_count_mismatch_rejected():
    with pytest.raises(InputValidationError):
        validate_distance_matrix(LABELS, GOOD[:2], max_taxa=10)


def test_duplicate_labels_rejected():
    with pytest.raises(InputValidationError) as exc:
        validate_distance_matrix(["A", "A", "C"], GOOD, max_taxa=10)
    assert "duplicate taxon label" in exc.value.message


def test_blank_label_rejected():
    with pytest.raises(InputValidationError):
        validate_distance_matrix(["A", "  ", "C"], GOOD, max_taxa=10)


def test_too_few_taxa_rejected():
    with pytest.raises(InputValidationError):
        validate_distance_matrix(["A"], [[0.0]], max_taxa=10)


def test_max_taxa_exceeded_is_resource_exhausted():
    with pytest.raises(ResourceExhaustedError) as exc:
        validate_distance_matrix(LABELS, GOOD, max_taxa=2)
    assert exc.value.category is ErrorCategory.RESOURCE_EXHAUSTED
    assert exc.value.details == {"got": 3, "max_taxa": 2}
