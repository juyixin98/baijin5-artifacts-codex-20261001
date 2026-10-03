"""Data contracts shared across module boundaries.

Layers:
    api      -> parses HTTP payloads into these contracts (see ``specs``)
    specs    -> builds :class:`EnergySpec` from request contracts
    energy   -> validates + independently evaluates an :class:`EnergySpec`
    graph    -> lowers an :class:`EnergySpec` to an s-t flow network
    solver   -> max-flow / min-cut on the network
    verify   -> cut certificate + independent cross-checks

Conventions:
    * label 0 = background = source side of the cut
    * label 1 = foreground = sink side of the cut
    * pixels are row-major indices ``r * width + c``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from .errors import InputValidationError

LABEL_BACKGROUND = 0
LABEL_FOREGROUND = 1


@dataclass(frozen=True)
class ImageContract:
    """A grayscale image normalized to float64 in [0, 1], shape (H, W)."""

    width: int
    height: int
    pixels: np.ndarray  # shape (height, width), float64, values in [0, 1]

    @staticmethod
    def from_array(pixels: np.ndarray) -> "ImageContract":
        arr = np.asarray(pixels, dtype=np.float64)
        if arr.ndim != 2:
            raise InputValidationError(
                "bad_image_shape",
                f"image must be 2-D grayscale, got shape {arr.shape}",
            )
        if arr.size == 0:
            raise InputValidationError("bad_image_shape", "image is empty")
        if not np.all(np.isfinite(arr)):
            raise InputValidationError(
                "bad_image_values", "image contains NaN or infinite values"
            )
        if arr.min() < 0.0 or arr.max() > 1.0:
            raise InputValidationError(
                "bad_image_values",
                "image intensities must be normalized to [0, 1]",
                details={"min": float(arr.min()), "max": float(arr.max())},
            )
        height, width = arr.shape
        return ImageContract(width=width, height=height, pixels=arr)

    @staticmethod
    def from_png(path: Path) -> "ImageContract":
        if not path.is_file():
            raise InputValidationError(
                "image_not_found", f"no image fixture at {path}"
            )
        with Image.open(path) as img:
            gray = np.asarray(img.convert("L"), dtype=np.float64) / 255.0
        return ImageContract.from_array(gray)


@dataclass(frozen=True)
class PairwiseTerm:
    """One pairwise potential on the unordered pixel pair ``(p, q)``.

    ``v_ab`` is the cost of ``(label_p, label_q) = (a, b)``. Submodularity
    (checked in :mod:`graphcut.energy`) requires
    ``v00 + v11 <= v01 + v10``; non-submodular terms are rejected, never
    silently repaired.
    """

    p: int
    q: int
    v00: float
    v01: float
    v10: float
    v11: float


@dataclass(frozen=True)
class SeedSet:
    """Hard constraints: pixel indices forced to a label."""

    foreground: tuple[int, ...] = ()
    background: tuple[int, ...] = ()


@dataclass(frozen=True)
class EnergySpec:
    """Fully materialized two-label energy.

    E(x) = sum_p D_p(x_p) + sum_(p,q) V_pq(x_p, x_q)

    with ``D_p(0) = unary0[p]``, ``D_p(1) = unary1[p]`` and pairwise terms
    as explicit :class:`PairwiseTerm` entries. All values must be finite and
    non-negative; validated by :func:`graphcut.energy.validate_spec`.
    """

    width: int
    height: int
    unary0: np.ndarray  # shape (height, width), float64, >= 0
    unary1: np.ndarray  # shape (height, width), float64, >= 0
    pairwise: tuple[PairwiseTerm, ...]
    seeds: SeedSet = field(default_factory=SeedSet)

    @property
    def num_pixels(self) -> int:
        return self.width * self.height


@dataclass(frozen=True)
class EnergyDecomposition:
    """Energy of a labeling, split by term type."""

    data: float
    smoothness: float
    total: float


@dataclass(frozen=True)
class CutCertificate:
    """Optimality certificate for a solved instance.

    Invariants (checked in :mod:`graphcut.verify`):
        * ``flow_value == cut_capacity`` (max-flow min-cut duality)
        * ``energy_total == graph_constant + flow_value`` (graph reduction
          faithfully encodes the energy)
        * every hard seed carries its forced label
        * optional SciPy cross-check agrees on the flow value
    """

    flow_value: float
    cut_capacity: float
    graph_constant: float
    energy: EnergyDecomposition
    seeds_satisfied: bool
    scipy_flow_value: float | None
    consistent: bool
    tolerance: float


@dataclass(frozen=True)
class SolveResult:
    """Full output of one segmentation run."""

    run_id: str
    width: int
    height: int
    labeling: np.ndarray  # shape (height, width), values in {0, 1}
    energy: EnergyDecomposition
    certificate: CutCertificate
    stats: dict[str, float]
