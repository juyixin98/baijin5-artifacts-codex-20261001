"""Input contracts: validated, immutable problem descriptions.

This module is the boundary between untrusted request data and the numerical
kernel.  Everything the kernel receives is a :class:`SegmentationSpec` whose
invariants have already been checked:

- unary (data) terms are finite and non-negative;
- the pairwise table is finite, non-negative and *submodular*
  (``v00 + v11 <= v01 + v10``) — non-submodular potentials are rejected,
  never silently repaired (no abs()/clamping tricks);
- hard seeds are inside the image and mutually consistent — a pixel seeded
  as both foreground and background is a STATE_CONFLICT, not an input typo;
- the image respects configured resource limits.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .config import Settings
from .errors import InputValidationError, ResourceExhaustedError, StateConflictError

FOREGROUND = 1
BACKGROUND = 0

_SUBMOD_TOL = 1e-12


@dataclass(frozen=True)
class PairwiseSpec:
    """Binary pairwise potential V(label_p, label_q) for 4-neighbour edges."""

    v00: float
    v01: float
    v10: float
    v11: float

    @property
    def submodular_slack(self) -> float:
        """(v01 + v10) - (v00 + v11); >= 0 iff submodular."""
        return (self.v01 + self.v10) - (self.v00 + self.v11)

    def cost(self, label_p: int, label_q: int) -> float:
        if label_p == 0:
            return self.v00 if label_q == 0 else self.v01
        return self.v10 if label_q == 0 else self.v11


@dataclass(frozen=True)
class Seed:
    row: int
    col: int
    label: int  # FOREGROUND or BACKGROUND


@dataclass(frozen=True)
class SegmentationSpec:
    """Fully validated segmentation problem."""

    height: int
    width: int
    unary0: np.ndarray  # cost of label 0 per pixel, shape (H, W), finite, >= 0
    unary1: np.ndarray  # cost of label 1 per pixel, shape (H, W), finite, >= 0
    pairwise: PairwiseSpec
    seeds: tuple[Seed, ...]

    @property
    def num_pixels(self) -> int:
        return self.height * self.width


def validate_pairwise(v00: float, v01: float, v10: float, v11: float) -> PairwiseSpec:
    values = {"v00": v00, "v01": v01, "v10": v10, "v11": v11}
    for name, value in values.items():
        if not math.isfinite(value):
            raise InputValidationError(
                f"pairwise term {name} must be finite, got {value!r}",
                code="NON_FINITE_SMOOTHNESS_TERM",
                details={"term": name, "value": value},
            )
        if value < 0.0:
            raise InputValidationError(
                f"pairwise term {name} must be non-negative, got {value!r}",
                code="NEGATIVE_SMOOTHNESS_TERM",
                details={"term": name, "value": value},
            )
    spec = PairwiseSpec(v00=v00, v01=v01, v10=v10, v11=v11)
    if spec.submodular_slack < -_SUBMOD_TOL:
        raise InputValidationError(
            "pairwise potential is not submodular: "
            f"v00 + v11 = {v00 + v11!r} > v01 + v10 = {v01 + v10!r}; "
            "graph-cut minimization is only valid for submodular potentials "
            "and this service refuses to approximate (e.g. by abs())",
            code="NON_SUBMODULAR_POTENTIAL",
            details={**values, "slack": spec.submodular_slack},
        )
    return spec


def _validate_unary(name: str, arr: np.ndarray, height: int, width: int) -> np.ndarray:
    a = np.asarray(arr, dtype=np.float64)
    if a.shape != (height, width):
        raise InputValidationError(
            f"{name} must have shape ({height}, {width}), got {a.shape}",
            code="UNARY_SHAPE_MISMATCH",
            details={"term": name, "expected": [height, width], "got": list(a.shape)},
        )
    if not np.all(np.isfinite(a)):
        raise InputValidationError(
            f"{name} contains non-finite values",
            code="NON_FINITE_DATA_TERM",
            details={"term": name},
        )
    if float(a.min()) < 0.0:
        raise InputValidationError(
            f"{name} (data term) must be non-negative; min={float(a.min())!r}",
            code="NEGATIVE_DATA_TERM",
            details={"term": name, "min": float(a.min())},
        )
    return np.ascontiguousarray(a)


def validate_seeds(seeds: list[Seed], height: int, width: int) -> tuple[Seed, ...]:
    seen: dict[tuple[int, int], int] = {}
    for seed in seeds:
        if seed.label not in (FOREGROUND, BACKGROUND):
            raise InputValidationError(
                f"seed label must be 0 or 1, got {seed.label!r}",
                code="SEED_LABEL_INVALID",
                details={"row": seed.row, "col": seed.col, "label": seed.label},
            )
        if not (0 <= seed.row < height and 0 <= seed.col < width):
            raise InputValidationError(
                f"seed ({seed.row}, {seed.col}) outside image of shape ({height}, {width})",
                code="SEED_OUT_OF_BOUNDS",
                details={"row": seed.row, "col": seed.col, "height": height, "width": width},
            )
        key = (seed.row, seed.col)
        previous = seen.get(key)
        if previous is not None and previous != seed.label:
            raise StateConflictError(
                f"pixel ({seed.row}, {seed.col}) seeded as both label {previous} "
                f"and label {seed.label}",
                code="SEED_CONFLICT",
                details={"row": seed.row, "col": seed.col,
                         "labels": sorted({previous, seed.label})},
            )
        seen[key] = seed.label
    # Deterministic order, duplicates removed.
    return tuple(Seed(row=r, col=c, label=label)
                 for (r, c), label in sorted(seen.items()))


def build_spec(
    *,
    height: int,
    width: int,
    unary0: np.ndarray,
    unary1: np.ndarray,
    pairwise: PairwiseSpec,
    seeds: list[Seed] | tuple[Seed, ...] = (),
    settings: Settings | None = None,
) -> SegmentationSpec:
    settings = settings or Settings()
    if height <= 0 or width <= 0:
        raise InputValidationError(
            f"image dimensions must be positive, got ({height}, {width})",
            code="IMAGE_SHAPE_INVALID",
            details={"height": height, "width": width},
        )
    if height * width > settings.max_pixels:
        raise ResourceExhaustedError(
            f"image has {height * width} pixels, limit is {settings.max_pixels}",
            code="IMAGE_TOO_LARGE",
            details={"pixels": height * width, "limit": settings.max_pixels},
        )
    u0 = _validate_unary("unary0", unary0, height, width)
    u1 = _validate_unary("unary1", unary1, height, width)
    clean_seeds = validate_seeds(list(seeds), height, width)
    return SegmentationSpec(
        height=height, width=width,
        unary0=u0, unary1=u1,
        pairwise=pairwise, seeds=clean_seeds,
    )


def neighbor_pairs(height: int, width: int) -> list[tuple[int, int]]:
    """Flat indices of 4-neighbour edges, each undirected edge listed once.

    Order: all horizontal edges row by row, then all vertical edges.
    Boundary pixels simply have fewer neighbours — no wrap-around.
    """
    pairs: list[tuple[int, int]] = []
    for r in range(height):
        for c in range(width - 1):
            p = r * width + c
            pairs.append((p, p + 1))
    for r in range(height - 1):
        for c in range(width):
            p = r * width + c
            pairs.append((p, p + width))
    return pairs
