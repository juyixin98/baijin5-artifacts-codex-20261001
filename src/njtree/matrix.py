"""Distance-matrix validation boundary.

Contract
--------
Input:  raw ``labels`` (list of str) and ``values`` (list of lists of numbers).
Output: a validated ``DistanceMatrix`` (numpy float64, C-contiguous copy).
Errors: ``InputValidationError`` for the first violated check,
        ``ResourceExhaustedError`` when ``n > max_taxa``.

Checks run in a declared, documented order and the *first* failing check is
the one raised (``collect_violations`` returns everything it can for the
validation endpoint):

1. labels        -- non-empty list, unique, charset [A-Za-z0-9_.-]
2. min_taxa      -- n >= 3 (NJ is undefined below 3)
3. max_taxa      -- n <= max_taxa  (resource guard, category=resource_exhausted)
4. shape         -- numeric, square, matches labels
5. finite        -- no NaN / inf
6. non_negative  -- all entries >= 0
7. zero_diagonal -- diagonal exactly 0
8. symmetric     -- max |D - D.T| <= symmetry_tol

Non-additive matrices are *not* rejected: additivity is a property NJ
approximates, and the fit residual is reported by the service instead.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

import numpy as np

from .errors import ErrorCategory, InputValidationError, ResourceExhaustedError
from .models import DEFAULT_MAX_TAXA, DistanceMatrix

LABEL_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
DEFAULT_SYMMETRY_TOL = 1e-9

CHECK_ORDER = (
    "labels",
    "min_taxa",
    "max_taxa",
    "shape",
    "finite",
    "non_negative",
    "zero_diagonal",
    "symmetric",
)

_MAX_REPORTED_INDICES = 10


def _violation(check: str, category: ErrorCategory, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"check": check, "category": category.value, "message": message, "details": details or {}}


def collect_violations(
    labels: Sequence[Any],
    values: Any,
    *,
    max_taxa: int = DEFAULT_MAX_TAXA,
    symmetry_tol: float = DEFAULT_SYMMETRY_TOL,
) -> list[dict[str, Any]]:
    """Run the declared checks in order and return every violation found.

    Checks whose precondition failed (e.g. symmetry after a shape error)
    are skipped rather than reported spuriously.
    """
    inp = ErrorCategory.INPUT_ERROR

    # 1. labels
    if not isinstance(labels, (list, tuple)) or len(labels) == 0:
        return [_violation("labels", inp, "labels must be a non-empty list")]
    bad_labels = [x for x in labels if not isinstance(x, str) or not LABEL_RE.match(x)]
    if bad_labels:
        return [_violation("labels", inp, "labels must match [A-Za-z0-9_.-]+ and be non-empty",
                           {"invalid": [str(x) for x in bad_labels[:_MAX_REPORTED_INDICES]]})]
    seen: set[str] = set()
    duplicates: set[str] = set()
    for label in labels:
        if label in seen:
            duplicates.add(label)
        seen.add(label)
    if duplicates:
        return [_violation("labels", inp, "duplicate labels", {"duplicates": sorted(duplicates)})]

    n = len(labels)

    # 2. min taxa
    if n < 3:
        return [_violation("min_taxa", inp, f"neighbor joining requires at least 3 taxa, got {n}")]
    # 3. max taxa (resource guard)
    if n > max_taxa:
        return [_violation("max_taxa", ErrorCategory.RESOURCE_EXHAUSTED,
                           f"too many taxa: {n} > max_taxa={max_taxa}",
                           {"n": n, "max_taxa": max_taxa})]

    # 4. shape
    try:
        arr = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        return [_violation("shape", inp, f"matrix is not numeric: {exc}")]
    if arr.shape != (n, n):
        return [_violation("shape", inp,
                           f"matrix shape {arr.shape} does not match {n} labels (expected {(n, n)})")]

    # 5. finite
    if not bool(np.isfinite(arr).all()):
        idx = np.argwhere(~np.isfinite(arr))[:_MAX_REPORTED_INDICES].tolist()
        return [_violation("finite", inp, "matrix contains NaN or infinite values", {"indices": idx})]

    violations: list[dict[str, Any]] = []

    # 6. non-negative
    neg = np.argwhere(arr < 0)
    if len(neg):
        violations.append(_violation("non_negative", inp, "matrix contains negative distances",
                                     {"count": int(len(neg)), "indices": neg[:_MAX_REPORTED_INDICES].tolist()}))
    # 7. zero diagonal
    diag_idx = np.argwhere(np.diag(arr) != 0.0).flatten()
    if len(diag_idx):
        violations.append(_violation("zero_diagonal", inp, "diagonal entries must be exactly 0",
                                     {"indices": diag_idx[:_MAX_REPORTED_INDICES].tolist()}))
    # 8. symmetric
    max_asym = float(np.abs(arr - arr.T).max())
    if max_asym > symmetry_tol:
        violations.append(_violation("symmetric", inp,
                                     f"matrix is not symmetric (max |D-D^T| = {max_asym:.6g} > {symmetry_tol:.6g})",
                                     {"max_asymmetry": max_asym, "tolerance": symmetry_tol}))
    return violations


def validate_distance_matrix(
    labels: Sequence[str],
    values: Any,
    *,
    max_taxa: int = DEFAULT_MAX_TAXA,
    symmetry_tol: float = DEFAULT_SYMMETRY_TOL,
) -> DistanceMatrix:
    """Validate raw input and return an immutable-by-convention DistanceMatrix.

    Raises the first violation in CHECK_ORDER; resource violations raise
    ResourceExhaustedError, everything else InputValidationError.
    """
    violations = collect_violations(labels, values, max_taxa=max_taxa, symmetry_tol=symmetry_tol)
    if violations:
        first = violations[0]
        exc_type = ResourceExhaustedError if first["category"] == ErrorCategory.RESOURCE_EXHAUSTED.value else InputValidationError
        raise exc_type(
            first["message"],
            details={"check": first["check"], **first["details"], "all_violations": violations},
        )
    return DistanceMatrix(tuple(labels), np.array(values, dtype=np.float64))
