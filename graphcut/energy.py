"""Independent energy evaluation.

The energy of a labeling x in {0,1}^N is

    E(x) = sum_p U_p(x_p) + sum_{(p,q) in N4} V(x_p, x_q)

evaluated *directly from the spec* — it shares no code with the graph
builder, so the cut certificate can cross-check the solver output against
an independent computation of the same quantity.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .contracts import SegmentationSpec, neighbor_pairs
from .errors import InputValidationError


@dataclass(frozen=True)
class EnergyBreakdown:
    data: float
    smooth: float

    @property
    def total(self) -> float:
        return self.data + self.smooth

    def to_dict(self) -> dict[str, float]:
        return {"data": self.data, "smooth": self.smooth, "total": self.total}


def validate_labels(spec: SegmentationSpec, labels: np.ndarray) -> np.ndarray:
    lab = np.asarray(labels)
    if lab.shape != (spec.height, spec.width):
        raise InputValidationError(
            f"labels must have shape ({spec.height}, {spec.width}), got {lab.shape}",
            code="LABELS_SHAPE_MISMATCH",
            details={"expected": [spec.height, spec.width], "got": list(lab.shape)},
        )
    unique = np.unique(lab)
    if not set(unique.tolist()) <= {0, 1}:
        raise InputValidationError(
            f"labels must be in {{0, 1}}, got values {unique.tolist()}",
            code="LABELS_VALUE_INVALID",
            details={"values": [int(v) for v in unique.tolist()]},
        )
    return lab.astype(np.int8, copy=False)


def evaluate_energy(spec: SegmentationSpec, labels: np.ndarray) -> EnergyBreakdown:
    """Direct O(N + E) evaluation of the energy of ``labels``."""
    lab = validate_labels(spec, labels)
    flat = lab.ravel()
    u0 = spec.unary0.ravel()
    u1 = spec.unary1.ravel()
    data = float(np.dot(u0, 1.0 - flat) + np.dot(u1, flat))

    smooth = 0.0
    pw = spec.pairwise
    for p, q in neighbor_pairs(spec.height, spec.width):
        smooth += pw.cost(int(flat[p]), int(flat[q]))
    return EnergyBreakdown(data=data, smooth=smooth)
