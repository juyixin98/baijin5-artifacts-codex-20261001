"""Coefficient normalization and stability validation for SOS cascades.

Each section is a row ``[b0, b1, b2, a0, a1, a2]`` describing

    y[n] = (b0 x[n] + b1 x[n-1] + b2 x[n-2] - a1 y[n-1] - a2 y[n-2]) / a0

Normalization divides every section by its ``a0`` so the cascade core can
assume ``a0 == 1``. Rejection policy:

- ``a0 == 0`` (or non-finite)            -> CoefficientError
- any non-finite coefficient             -> CoefficientError
- any pole with radius >= limit (1.0)    -> CoefficientError (unstable)
- more sections than the configured cap  -> ResourceLimitError
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.errors import CoefficientError, ResourceLimitError

SECTION_WIDTH = 6  # b0, b1, b2, a0, a1, a2


@dataclass(frozen=True)
class NormalizedSos:
    """Validated, a0-normalized cascade coefficients plus diagnostics."""

    sections: np.ndarray  # shape (n_sections, 6), every row has a0 == 1
    max_pole_radius: float

    @property
    def n_sections(self) -> int:
        return int(self.sections.shape[0])


def section_pole_radii(a1: float, a2: float) -> np.ndarray:
    """Pole magnitudes of one normalized denominator [1, a1, a2]."""
    return np.abs(np.roots([1.0, a1, a2]))


def normalize_and_validate(
    sections: np.ndarray | list,
    *,
    max_sections: int,
    pole_radius_limit: float = 1.0,
) -> NormalizedSos:
    """Validate raw SOS rows and return them normalized by a0.

    Raises:
        ResourceLimitError: too many sections.
        CoefficientError: bad shape, non-finite values, a0 == 0, or
            unstable / marginally stable poles.
    """
    sos = np.asarray(sections, dtype=np.float64)
    if sos.ndim != 2 or sos.shape[1] != SECTION_WIDTH or sos.shape[0] == 0:
        raise CoefficientError(
            "sections must be a non-empty (n, 6) array of [b0,b1,b2,a0,a1,a2]",
            context={"shape": list(sos.shape)},
        )
    if sos.shape[0] > max_sections:
        raise ResourceLimitError(
            f"too many sections: {sos.shape[0]} > limit {max_sections}",
            context={"n_sections": int(sos.shape[0]), "limit": max_sections},
        )
    if not np.all(np.isfinite(sos)):
        raise CoefficientError("coefficients must all be finite (no NaN/Inf)")

    a0 = sos[:, 3]
    if np.any(a0 == 0.0):
        bad = int(np.nonzero(a0 == 0.0)[0][0])
        raise CoefficientError(
            f"section {bad} has a0 == 0; cannot normalize",
            context={"section": bad},
        )

    normalized = sos / a0[:, None]

    max_radius = 0.0
    for idx, row in enumerate(normalized):
        radii = section_pole_radii(row[4], row[5])
        radius = float(np.max(radii)) if radii.size else 0.0
        max_radius = max(max_radius, radius)
        if not np.isfinite(radius):
            raise CoefficientError(
                f"section {idx} pole computation failed",
                context={"section": idx},
            )
        if radius >= pole_radius_limit:
            raise CoefficientError(
                f"section {idx} unstable: pole radius {radius:.6f} >= "
                f"limit {pole_radius_limit}",
                context={"section": idx, "pole_radius": radius},
            )

    return NormalizedSos(sections=normalized, max_pole_radius=max_radius)
