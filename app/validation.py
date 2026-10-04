"""Distance-matrix preconditions.

These checks run BEFORE any tree building, in this order:
labels -> shape -> finiteness -> non-negativity -> zero diagonal -> symmetry.
Non-additivity is NOT checked here: additive fit is a property of the output,
reported through the residual report, not a precondition.

Symmetry and the zero diagonal are checked with exact equality by default:
inputs are synthetic fixtures or JSON numbers, and a tolerance would let
quietly-wrong matrices through. A caller that genuinely needs a tolerance
can pass one explicitly.
"""

from __future__ import annotations

import math

from .errors import InputValidationError, ResourceExhaustedError


def validate_labels(labels: list[str], max_taxa: int) -> int:
    n = len(labels)
    if n < 2:
        raise InputValidationError(
            "at least 2 taxa are required", {"got": n}
        )
    if n > max_taxa:
        raise ResourceExhaustedError(
            "number of taxa exceeds the configured limit",
            {"got": n, "max_taxa": max_taxa},
        )
    seen: set[str] = set()
    for idx, label in enumerate(labels):
        if not isinstance(label, str) or not label.strip():
            raise InputValidationError(
                "taxon labels must be non-empty strings",
                {"index": idx, "label": label},
            )
        if label in seen:
            raise InputValidationError(
                "duplicate taxon label",
                {"label": label, "index": idx},
            )
        seen.add(label)
    return n


def validate_distance_matrix(
    labels: list[str],
    matrix: list[list[float]],
    max_taxa: int,
    symmetry_tol: float = 0.0,
) -> list[list[float]]:
    """Validate and return the matrix as plain float lists (no mutation)."""
    n = validate_labels(labels, max_taxa)

    if len(matrix) != n:
        raise InputValidationError(
            "distance matrix row count does not match the label count",
            {"rows": len(matrix), "labels": n},
        )
    for i, row in enumerate(matrix):
        if len(row) != n:
            raise InputValidationError(
                "distance matrix is not square",
                {"row": i, "row_length": len(row), "expected": n},
            )

    m = [[float(v) for v in row] for row in matrix]

    for i in range(n):
        for j in range(n):
            v = m[i][j]
            if not math.isfinite(v):
                raise InputValidationError(
                    "distance matrix contains a non-finite value",
                    {"row": i, "col": j, "value": matrix[i][j]},
                )
            if v < 0:
                raise InputValidationError(
                    "distance matrix contains a negative distance",
                    {"row": i, "col": j, "value": v},
                )
    for i in range(n):
        if m[i][i] != 0.0:
            raise InputValidationError(
                "distance matrix diagonal is not zero",
                {"row": i, "value": m[i][i]},
            )
    for i in range(n):
        for j in range(i + 1, n):
            if abs(m[i][j] - m[j][i]) > symmetry_tol:
                raise InputValidationError(
                    "distance matrix is not symmetric",
                    {
                        "row": i,
                        "col": j,
                        "upper": m[i][j],
                        "lower": m[j][i],
                        "tolerance": symmetry_tol,
                    },
                )
    return m
