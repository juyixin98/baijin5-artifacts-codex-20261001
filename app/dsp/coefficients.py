"""Coefficient normalization and stability validation for SOS cascades.

Contract
--------
Input is a second-order-section cascade: an ``(n_sections, 6)`` array-like
where each row is ``[b0, b1, b2, a0, a1, a2]``.

Validation rules (all violations raise ``CoefficientValidationError``):

1. Shape must be ``(n_sections, 6)`` with ``1 <= n_sections <= MAX_SECTIONS``.
2. Every coefficient must be finite.
3. ``|a0|`` must exceed ``A0_ABS_TOL`` — a zero (or denormal) ``a0`` makes the
   section non-normalizable and is rejected, never silently patched.
4. After normalization (dividing the row by ``a0`` so ``a0 == 1``), the poles
   of each section — the roots of ``z^2 + a1 z + a2`` — must all satisfy
   ``|p| < POLE_RADIUS_LIMIT``. Poles on or outside the unit circle are
   rejected; poles arbitrarily close to it are accepted.

The returned ``NormalizedSOS`` is immutable; downstream code never re-validates.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.config import A0_ABS_TOL, MAX_SECTIONS, POLE_RADIUS_LIMIT
from app.errors import CoefficientValidationError, ResourceLimitError


@dataclass(frozen=True)
class NormalizedSOS:
    """Validated, a0-normalized SOS cascade."""

    sections: np.ndarray  # shape (n_sections, 6), column 3 (a0) is all 1.0
    pole_radii: tuple[float, ...]  # max |pole| per section, all < limit

    @property
    def n_sections(self) -> int:
        return int(self.sections.shape[0])

    @property
    def max_pole_radius(self) -> float:
        return max(self.pole_radii)


def _section_pole_radius(a1: float, a2: float) -> float:
    """Largest |root| of z^2 + a1*z + a2 (1.0 for a degenerate section)."""
    roots = np.roots([1.0, a1, a2])
    if roots.size == 0:
        return 0.0
    return float(np.max(np.abs(roots)))


def normalize_and_validate(sos: object) -> NormalizedSOS:
    """Validate and normalize an SOS cascade.

    Raises:
        CoefficientValidationError: bad shape, non-finite values, zero a0,
            or unstable poles.
        ResourceLimitError: too many sections.
    """
    try:
        arr = np.asarray(sos, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise CoefficientValidationError(
            "sos must be a numeric (n_sections, 6) array-like",
            detail={"reason": str(exc)},
        ) from exc

    if arr.ndim != 2 or arr.shape[1] != 6:
        raise CoefficientValidationError(
            "sos must have shape (n_sections, 6): [b0, b1, b2, a0, a1, a2] per row",
            detail={"received_shape": list(arr.shape)},
        )
    if arr.shape[0] < 1:
        raise CoefficientValidationError("sos must contain at least one section")
    if arr.shape[0] > MAX_SECTIONS:
        raise ResourceLimitError(
            f"too many sections: {arr.shape[0]} > {MAX_SECTIONS}",
            detail={"n_sections": int(arr.shape[0]), "limit": MAX_SECTIONS},
        )
    if not np.isfinite(arr).all():
        bad = np.argwhere(~np.isfinite(arr))
        raise CoefficientValidationError(
            "sos contains non-finite coefficients",
            detail={"first_bad_index": [int(bad[0][0]), int(bad[0][1])]},
        )

    a0 = arr[:, 3]
    if np.any(np.abs(a0) <= A0_ABS_TOL):
        idx = int(np.argmin(np.abs(a0)))
        raise CoefficientValidationError(
            f"a0 of section {idx} is zero (|a0| <= {A0_ABS_TOL}); cannot normalize",
            detail={"section": idx, "a0": float(a0[idx])},
        )

    normalized = arr / a0[:, None]

    pole_radii: list[float] = []
    for i, row in enumerate(normalized):
        radius = _section_pole_radius(row[4], row[5])
        if not radius < POLE_RADIUS_LIMIT:
            raise CoefficientValidationError(
                f"section {i} is unstable: pole radius {radius:.17g} "
                f">= {POLE_RADIUS_LIMIT}",
                detail={
                    "section": i,
                    "pole_radius": radius,
                    "limit": POLE_RADIUS_LIMIT,
                    "a": [1.0, float(row[4]), float(row[5])],
                },
            )
        pole_radii.append(radius)

    return NormalizedSOS(sections=normalized, pole_radii=tuple(pole_radii))
